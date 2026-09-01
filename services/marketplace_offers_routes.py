"""Marketplace offers — the Phase 3 route pack.

The state machine here is a deliberate mirror of the mobile module
`mobile-native/src/api/marketplaceOffers.ts`, which was written and tested
before any backend existed. That module is the contract: states
open / accepted / countered / declined / expired / withdrawn, a 72-hour TTL,
counters modelled as *new* offers chained through `counter_of` (a seller cannot
rewrite what a buyer said), and expiry computed from the clock at read time
rather than stored — so no sweeper job is required and an offer that lapsed
while the app was closed is already expired when the list renders.

Rules enforced server-side (mirroring the mobile reducer, because the client
copy defends the UI and this copy defends the data):

- Only `open` offers transition. Acting on a resolved offer returns
  `already_resolved` with the settled state — a retry after a dropped response
  lands on the truth, not an error.
- `accept`/`decline`/`counter` belong to the recipient; `withdraw` belongs to
  the author. Wrong-side actions return 403.
- A seller's minimum-acceptable price is never serialized to buyers (there is
  deliberately no such column here — if one is added it must never appear in a
  buyer-facing payload).
- Accepting opens a time-limited checkout window (24h). Checkout charges the
  *offered* amount through the same seller_transactions + Stripe Connect
  surface as every other purchase — never a new payment path.
"""

from __future__ import annotations

import json
import logging
import secrets
from datetime import datetime, timedelta, timezone

from flask import Blueprint, jsonify, request

from services import marketplace_listing_lifecycle as listing_lifecycle
from services import marketplace_seller_identity as seller_identity
# The offers lane settles through the cart's helpers rather than its own copies.
# Three checkout entry points with three private reservation implementations is
# how one of them ends up overselling while the other two look correct.
from services.marketplace_cart_routes import (
    _apple_pay_merchant_id,
    _listing_metadata,
    release_inventory_reservation,
    stripe_shipping_checkout_params,
)
from services import marketplace_fulfillment
from services.marketplace_payment_errors import (
    below_minimum_charge_error,
    classify_provider_exception,
    stripe_response_value,
)
from services import marketplace_quote_service
from services import marketplace_goods_policy
from services import marketplace_payment_pause

LOGGER = logging.getLogger(__name__)

offers_blueprint = Blueprint("pulse_marketplace_offers", __name__)

API_PREFIX = "/api/pulse/marketplace/offers"

OFFER_TTL_HOURS = 72          # mirrors OFFER_TTL_HOURS in marketplaceOffers.ts
ACCEPT_CHECKOUT_HOURS = 24    # how long an accepted offer is checkout-able
MAX_OPEN_OFFERS_PER_LISTING = 5

_SCHEMA_READY = False


def _bot():
    import bot

    return bot


def _now_dt() -> datetime:
    return datetime.now(timezone.utc)


def _now() -> str:
    return _now_dt().isoformat(timespec="seconds")


def _json(payload, status: int = 200):
    response = jsonify(payload)
    response.headers["Cache-Control"] = "no-store, max-age=0, must-revalidate"
    return response, status


def _error(message: str, status: int = 400, **extra):
    return _json({"ok": False, "message": message, **extra}, status)


def _require_user():
    try:
        user = _bot().api_account_user()
    except Exception:
        LOGGER.exception("OFFERS_AUTH_LOOKUP_FAILED")
        user = None
    if not user:
        return None, _error("Login required.", 401)
    return user, None


def _with_db(handler):
    bot = _bot()
    conn = bot.db()
    try:
        try:
            import sqlite3

            conn.row_factory = sqlite3.Row
        except Exception:
            pass
        cur = conn.cursor()
        _ensure_schema(cur)
        result = handler(cur, conn)
        conn.commit()
        return result
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _ensure_schema(cur) -> None:
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS marketplace_offers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            listing_id INTEGER,
            buyer_user_id INTEGER,
            seller_user_id INTEGER,
            direction TEXT DEFAULT 'buyer_to_seller',
            amount_minor INTEGER DEFAULT 0,
            list_price_minor INTEGER DEFAULT 0,
            currency TEXT DEFAULT 'USD',
            qty INTEGER DEFAULT 1,
            note TEXT,
            state TEXT DEFAULT 'open',
            counter_of INTEGER,
            accepted_until TEXT,
            created_at TEXT,
            updated_at TEXT
        )
        """
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_marketplace_offers_listing ON marketplace_offers(listing_id, state)"
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_marketplace_offers_buyer ON marketplace_offers(buyer_user_id, state)"
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_marketplace_offers_seller ON marketplace_offers(seller_user_id, state)"
    )
    _SCHEMA_READY = True


# --------------------------------------------------------------------------
# Expiry (computed, never stored)
# --------------------------------------------------------------------------

def _effective_state(offer: dict) -> str:
    """`open` past its TTL reads as `expired`; an `accepted` offer past its
    checkout window also reads as `expired`. The row is only rewritten when it
    is next touched, which is enough — reads never trust the stored state of a
    stale row."""
    state = offer.get("state") or "open"
    now = _now_dt()
    if state == "open":
        created = _parse(offer.get("created_at"))
        if created and now > created + timedelta(hours=OFFER_TTL_HOURS):
            return "expired"
    if state == "accepted":
        until = _parse(offer.get("accepted_until"))
        if until and now > until:
            return "expired"
    return state


def _parse(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _serialize(offer: dict, viewer_id: int) -> dict:
    state = _effective_state(offer)
    return {
        "id": int(offer["id"]),
        "listing_id": int(offer.get("listing_id") or 0),
        "buyer_user_id": int(offer.get("buyer_user_id") or 0),
        "seller_user_id": int(offer.get("seller_user_id") or 0),
        "direction": offer.get("direction") or "buyer_to_seller",
        "amount_minor": int(offer.get("amount_minor") or 0),
        "list_price_minor": int(offer.get("list_price_minor") or 0),
        "currency": offer.get("currency") or "USD",
        "qty": int(offer.get("qty") or 1),
        "note": offer.get("note") or "",
        "state": state,
        "counter_of": int(offer["counter_of"]) if offer.get("counter_of") else None,
        "accepted_until": offer.get("accepted_until") or None,
        "created_at": offer.get("created_at") or "",
        "updated_at": offer.get("updated_at") or "",
        "mine": int(offer.get("buyer_user_id") or 0) == viewer_id
                if (offer.get("direction") or "buyer_to_seller") == "buyer_to_seller"
                else int(offer.get("seller_user_id") or 0) == viewer_id,
    }


def _load_offer(cur, offer_id: int) -> dict:
    cur.execute("SELECT * FROM marketplace_offers WHERE id=? LIMIT 1", (offer_id,))
    return dict(cur.fetchone() or {})


def _hydrate(cur, serialized: list) -> list:
    """Attach listing title/thumbnail and buyer display name so the client
    never renders an offer row it cannot caption. Batch lookups, not N+1."""
    listing_ids = {o["listing_id"] for o in serialized if o.get("listing_id")}
    buyer_ids = {o["buyer_user_id"] for o in serialized if o.get("buyer_user_id")}
    listings, buyers = {}, {}
    if listing_ids:
        marks = ",".join("?" for _ in listing_ids)
        cur.execute(
            f"SELECT id, title, cover_image_url FROM marketplace_listings WHERE id IN ({marks})",
            list(listing_ids),
        )
        listings = {int(r["id"]): dict(r) for r in (dict(x) for x in cur.fetchall())}
    if buyer_ids:
        marks = ",".join("?" for _ in buyer_ids)
        cur.execute(
            f"SELECT user_id, COALESCE(display_name, username, 'PulseSoc buyer') AS name FROM users WHERE user_id IN ({marks})",
            list(buyer_ids),
        )
        buyers = {int(r["user_id"]): dict(r) for r in (dict(x) for x in cur.fetchall())}
    for o in serialized:
        listing = listings.get(o.get("listing_id"), {})
        buyer = buyers.get(o.get("buyer_user_id"), {})
        o["item_title"] = listing.get("title") or "Marketplace item"
        o["item_thumbnail_url"] = listing.get("cover_image_url") or ""
        o["buyer_name"] = buyer.get("name") or "PulseSoc buyer"
    return serialized


def _serialize_full(cur, offer: dict, viewer_id: int) -> dict:
    return _hydrate(cur, [_serialize(offer, viewer_id)])[0]


def _author_id(offer: dict) -> int:
    return int(offer.get("buyer_user_id") or 0) \
        if (offer.get("direction") or "buyer_to_seller") == "buyer_to_seller" \
        else int(offer.get("seller_user_id") or 0)


def _recipient_id(offer: dict) -> int:
    return int(offer.get("seller_user_id") or 0) \
        if (offer.get("direction") or "buyer_to_seller") == "buyer_to_seller" \
        else int(offer.get("buyer_user_id") or 0)


# --------------------------------------------------------------------------
# Routes
# --------------------------------------------------------------------------

@offers_blueprint.route(API_PREFIX, methods=["POST"])
def offer_create():
    bot = _bot()
    bot.init_db()
    user, err = _require_user()
    if err:
        return err
    payload = request.get_json(silent=True) or {}
    listing_id = int(payload.get("listing_id") or 0)
    amount_minor = int(payload.get("amount_minor") or 0)
    qty = max(1, min(int(payload.get("qty") or 1), 20))
    note = bot.clean_html(str(payload.get("note") or ""))[:500]
    if not listing_id or amount_minor <= 0:
        return _error("An offer needs a listing and an amount.", 400)

    def handler(cur, conn):
        cur.execute("SELECT * FROM marketplace_listings WHERE id=? LIMIT 1", (listing_id,))
        listing = dict(cur.fetchone() or {})
        if not listing:
            return _error("Listing not found.", 404)
        seller_id = int(listing.get("seller_user_id") or 0)
        buyer_id = int(user["user_id"])
        if seller_id == buyer_id:
            return _error("You cannot make an offer on your own listing.", 400)
        if (listing.get("status") or "").lower() not in {"active", "approved"}:
            return _error("This listing is no longer available.", 409)
        list_price, currency = bot.parse_price_label_to_cents(
            listing.get("price_label") or "", listing.get("currency") or "USD"
        )
        cur.execute(
            """
            SELECT COUNT(*) AS n FROM marketplace_offers
            WHERE listing_id=? AND buyer_user_id=? AND state='open'
            """,
            (listing_id, buyer_id),
        )
        if int(dict(cur.fetchone() or {}).get("n") or 0) >= MAX_OPEN_OFFERS_PER_LISTING:
            return _error("You already have open offers on this listing.", 409)
        now = _now()
        cur.execute(
            """
            INSERT INTO marketplace_offers
                (listing_id, buyer_user_id, seller_user_id, direction, amount_minor,
                 list_price_minor, currency, qty, note, state, created_at, updated_at)
            VALUES (?, ?, ?, 'buyer_to_seller', ?, ?, ?, ?, ?, 'open', ?, ?)
            """,
            (listing_id, buyer_id, seller_id, amount_minor, int(list_price or 0),
             currency or "USD", qty, note, now, now),
        )
        offer = _load_offer(cur, int(cur.lastrowid))
        return _json({"ok": True, "offer": _serialize_full(cur, offer, buyer_id)})

    return _with_db(handler)


@offers_blueprint.route(API_PREFIX, methods=["GET"])
def offer_list():
    bot = _bot()
    bot.init_db()
    user, err = _require_user()
    if err:
        return err
    role = request.args.get("role") or "buyer"
    listing_id = int(request.args.get("listing_id") or 0)

    def handler(cur, conn):
        viewer = int(user["user_id"])
        where = ["seller_user_id=?"] if role == "seller" else ["buyer_user_id=?"]
        params = [viewer]
        if listing_id:
            where.append("listing_id=?")
            params.append(listing_id)
        cur.execute(
            f"SELECT * FROM marketplace_offers WHERE {' AND '.join(where)} ORDER BY id DESC LIMIT 200",
            params,
        )
        offers = _hydrate(cur, [_serialize(dict(r), viewer) for r in cur.fetchall()])
        return _json({"ok": True, "offers": offers,
                      "open_count": sum(1 for o in offers if o["state"] == "open")})

    return _with_db(handler)


def _transition(offer_id: int, action: str):
    """Shared body for accept / decline / withdraw."""
    bot = _bot()
    bot.init_db()
    user, err = _require_user()
    if err:
        return err

    def handler(cur, conn):
        viewer = int(user["user_id"])
        offer = _load_offer(cur, offer_id)
        if not offer:
            return _error("Offer not found.", 404)
        if viewer not in (int(offer.get("buyer_user_id") or 0), int(offer.get("seller_user_id") or 0)):
            return _error("Not your offer.", 403)
        state = _effective_state(offer)
        if state != "open":
            # Idempotent: a retry lands on the settled state, not an error page.
            return _json({"ok": False, "reason": "already_resolved",
                          "offer": _serialize_full(cur, offer, viewer)}, 409)
        if action == "withdraw" and viewer != _author_id(offer):
            return _error("Only the side that made the offer can withdraw it.", 403)
        if action in {"accept", "decline"} and viewer != _recipient_id(offer):
            return _error("Only the side that received the offer can do that.", 403)
        now = _now()
        new_state = {"accept": "accepted", "decline": "declined", "withdraw": "withdrawn"}[action]
        accepted_until = None
        if action == "accept":
            accepted_until = (_now_dt() + timedelta(hours=ACCEPT_CHECKOUT_HOURS)).isoformat(timespec="seconds")
        cur.execute(
            "UPDATE marketplace_offers SET state=?, accepted_until=?, updated_at=? WHERE id=? AND state='open'",
            (new_state, accepted_until, now, offer_id),
        )
        if not cur.rowcount:
            offer = _load_offer(cur, offer_id)
            return _json({"ok": False, "reason": "already_resolved",
                          "offer": _serialize_full(cur, offer, viewer)}, 409)
        offer = _load_offer(cur, offer_id)
        return _json({"ok": True, "offer": _serialize_full(cur, offer, viewer)})

    return _with_db(handler)


@offers_blueprint.route(f"{API_PREFIX}/<int:offer_id>/accept", methods=["POST"])
def offer_accept(offer_id: int):
    return _transition(offer_id, "accept")


@offers_blueprint.route(f"{API_PREFIX}/<int:offer_id>/decline", methods=["POST"])
def offer_decline(offer_id: int):
    return _transition(offer_id, "decline")


@offers_blueprint.route(f"{API_PREFIX}/<int:offer_id>/withdraw", methods=["POST"])
def offer_withdraw(offer_id: int):
    return _transition(offer_id, "withdraw")


@offers_blueprint.route(f"{API_PREFIX}/<int:offer_id>/counter", methods=["POST"])
def offer_counter(offer_id: int):
    """Close this offer as `countered` and open a fresh one the other way.
    The original is never edited — the chain is the audit trail."""
    bot = _bot()
    bot.init_db()
    user, err = _require_user()
    if err:
        return err
    payload = request.get_json(silent=True) or {}
    amount_minor = int(payload.get("amount_minor") or 0)
    note = bot.clean_html(str(payload.get("note") or ""))[:500]
    if amount_minor <= 0:
        return _error("A counter needs an amount.", 400)

    def handler(cur, conn):
        viewer = int(user["user_id"])
        offer = _load_offer(cur, offer_id)
        if not offer:
            return _error("Offer not found.", 404)
        if viewer != _recipient_id(offer):
            return _error("Only the side that received the offer can counter it.", 403)
        if _effective_state(offer) != "open":
            return _json({"ok": False, "reason": "already_resolved",
                          "offer": _serialize_full(cur, offer, viewer)}, 409)
        now = _now()
        cur.execute(
            "UPDATE marketplace_offers SET state='countered', updated_at=? WHERE id=? AND state='open'",
            (now, offer_id),
        )
        if not cur.rowcount:
            offer = _load_offer(cur, offer_id)
            return _json({"ok": False, "reason": "already_resolved",
                          "offer": _serialize_full(cur, offer, viewer)}, 409)
        new_direction = "seller_to_buyer" \
            if (offer.get("direction") or "buyer_to_seller") == "buyer_to_seller" \
            else "buyer_to_seller"
        cur.execute(
            """
            INSERT INTO marketplace_offers
                (listing_id, buyer_user_id, seller_user_id, direction, amount_minor,
                 list_price_minor, currency, qty, note, state, counter_of, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', ?, ?, ?)
            """,
            (offer.get("listing_id"), offer.get("buyer_user_id"), offer.get("seller_user_id"),
             new_direction, amount_minor, offer.get("list_price_minor"),
             offer.get("currency") or "USD", offer.get("qty") or 1, note, offer_id, now, now),
        )
        counter = _load_offer(cur, int(cur.lastrowid))
        return _json({"ok": True,
                      "offer": _serialize_full(cur, _load_offer(cur, offer_id), viewer),
                      "counter": _serialize_full(cur, counter, viewer)})

    return _with_db(handler)


@offers_blueprint.route(f"{API_PREFIX}/<int:offer_id>/checkout", methods=["POST"])
def offer_checkout(offer_id: int):
    """Buyer checks out an accepted offer at the offered amount, inside the
    acceptance window. Same Stripe surface as every other purchase."""
    bot = _bot()
    bot.init_db()
    user, err = _require_user()
    if err:
        return err
    def handler(cur, conn):
        buyer_id = int(user["user_id"])
        offer = _load_offer(cur, offer_id)
        if not offer:
            return _error("Offer not found.", 404)
        if buyer_id != int(offer.get("buyer_user_id") or 0):
            return _error("Only the buyer can check out an offer.", 403)
        state = _effective_state(offer)
        if state != "accepted":
            return _error("This offer is not open for checkout.", 409, state=state)
        seller_id = int(offer.get("seller_user_id") or 0)
        listing_id = int(offer.get("listing_id") or 0)
        payload = request.get_json(silent=True) or {}
        payment_mode_raw = payload.get("payment_mode")
        payment_mode = marketplace_payment_pause.normalize_marketplace_payment_mode(payment_mode_raw)
        if payment_mode == "card" and marketplace_payment_pause.marketplace_card_payments_paused():
            return _error(
                marketplace_payment_pause.MARKETPLACE_CARD_UNAVAILABLE_MESSAGE,
                503,
                error_code=marketplace_payment_pause.MARKETPLACE_CARD_UNAVAILABLE_CODE,
                error=marketplace_payment_pause.MARKETPLACE_CARD_UNAVAILABLE_CODE,
                **marketplace_payment_pause.card_unavailable_payload(),
            )
        cash_payment = payment_mode == "cash"
        # Same opt-in flag the cart lane uses. While Marketplace card collection
        # is paused, the guard above returns before this preserved Stripe path
        # can create a PaymentIntent or hosted Session.
        native_sheet = payment_mode == "card" and str(payment_mode_raw or "").strip().lower() == "payment_sheet"
        cur.execute(f"""SELECT l.*, COALESCE(ms.status,'missing') AS seller_status,
                   {seller_identity.store_name_select('ms')}
            FROM marketplace_listings l LEFT JOIN marketplace_sellers ms ON ms.user_id=l.seller_user_id
            WHERE l.id=? LIMIT 1""", (listing_id,))
        listing = dict(cur.fetchone() or {})
        if not listing or not listing_lifecycle.is_public(listing):
            return _error("The listing behind this offer is no longer available.", 409)
        goods_decision = marketplace_goods_policy.evaluate(listing)
        if goods_decision["decision"] != "ALLOWED":
            return _error("This listing requires Marketplace policy review.", 409,
                          code="LISTING_POLICY_BLOCKED", goods_policy=goods_decision)
        if bot.ios_native_app_request() and str(listing.get("product_type") or listing.get("listing_type") or listing.get("delivery_type") or "").lower() in {"digital", "course"}:
            return bot.ios_paid_digital_unavailable_response(api=True)
        approved = bot.approved_marketplace_seller_for_user(cur, seller_id)
        if not approved:
            return _error("Seller is not approved for payments.", 403)
        payout = bot.seller_payout_account(cur, seller_id, "merchant")
        fee_bps = marketplace_payment_pause.platform_fee_bps_for_marketplace_payment(
            bot.seller_fee_bps(cur, "merchant"),
            payment_mode,
        )
        qty = max(1, int(offer.get("qty") or 1))
        currency = offer.get("currency") or "USD"
        # An offer is negotiated on a listing of some type, and that type asks the
        # buyer the same questions here as it does on Buy Now. Haggling over the
        # price does not make the delivery address optional.
        listing_metadata = _listing_metadata(listing)
        fulfillment_kind = marketplace_fulfillment.resolve_kind(
            listing.get("listing_type") or listing.get("product_type"),
            listing.get("delivery_type"), listing_metadata,
        )
        fulfillment_kind, lane_error = marketplace_fulfillment.resolve_choice(
            fulfillment_kind, payload.get("fulfillment")
        )
        if lane_error:
            return _error("Choose how you want this order fulfilled before you pay.", 400, code=lane_error)
        commercial_quote = marketplace_quote_service.create_quote(
            listing_id=listing_id, seller_id=seller_id, quantity=qty,
            unit_price_minor=int(offer.get("amount_minor") or 0), currency=currency,
            live_fee_bps=fee_bps, offer_id=offer_id,
            offer_accepted_at=offer.get("responded_at") or offer.get("updated_at"),
            offer_expires_at=offer.get("accept_expires_at") or offer.get("expires_at"),
            shipping={"fulfillment": fulfillment_kind},
        )
        amount = commercial_quote["buyer_total_minor"]
        platform_fee = commercial_quote["platform_fee_minor"]
        # An accepted offer can be haggled below the floor even when the listing
        # price was above it, so this lane needs the same pre-flight as the
        # other two — refused before the transaction row and the stock hold.
        below_minimum = None if cash_payment else below_minimum_charge_error(amount, currency)
        if below_minimum:
            return _error(below_minimum["message"], below_minimum["status"],
                          code=below_minimum["code"], amount_cents=amount,
                          minimum_charge_cents=below_minimum["minimum_minor"])

        details_ok, details = marketplace_fulfillment.validate_details(
            fulfillment_kind, payload.get("fulfillment_details"), listing_metadata
        )
        if not details_ok:
            return _error(details["message"], details["status"],
                          code=details["code"], field=details.get("field", ""))
        fulfillment_snapshot = marketplace_fulfillment.snapshot(fulfillment_kind, details)
        stripe_shipping_object = marketplace_fulfillment.stripe_shipping(details)
        now = _now()
        initial_status = "cash_pending" if cash_payment else "created"
        payout_state = "cash_collect_in_person" if cash_payment else "pending_checkout"
        cur.execute(
            """
            INSERT INTO seller_transactions
            (buyer_user_id, seller_user_id, seller_type, item_type, item_id, amount_cents, currency,
             platform_fee_cents, seller_net_cents, status, metadata_json, created_at, updated_at)
            VALUES (?, ?, 'merchant', 'marketplace_product', ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (buyer_id, seller_id, listing_id, amount, currency, platform_fee,
             commercial_quote["seller_earnings_minor"], initial_status,
             marketplace_quote_service.transaction_metadata(
                 {"title": listing.get("title") or "Marketplace item",
                  "offer_id": offer_id, "qty": qty, "payment_method": payment_mode,
                  **({"fulfillment": fulfillment_snapshot} if fulfillment_snapshot else {})},
                 commercial_quote,
                 payout_state=payout_state),
             now, now),
        )
        tx_id = int(cur.lastrowid)

        if not cash_payment and not bot.STRIPE_SECRET_KEY:
            cur.execute("UPDATE seller_transactions SET status='blocked_stripe_not_configured', updated_at=? WHERE id=?", (now, tx_id))
            return _error("Stripe checkout is not configured yet. No card was charged.", 503,
                          code="PAYMENT_UNAVAILABLE", transaction_id=tx_id)

        # An accepted offer used to skip reservation entirely, so two buyers with
        # two accepted offers on a one-of-a-kind item could both reach Stripe.
        # Same keyed reservation the cart uses; released if the provider call fails.
        if fulfillment_kind not in marketplace_fulfillment.STOCKLESS_KINDS:
            cur.execute(
                "UPDATE marketplace_listings SET quantity=quantity-?, updated_at=? WHERE id=? AND quantity>=?",
                (qty, now, listing_id, qty),
            )
            if not cur.rowcount:
                cur.execute("UPDATE seller_transactions SET status='out_of_stock', updated_at=? WHERE id=?", (now, tx_id))
                return _error("This item sold out before checkout. No card was charged.", 409,
                              code="OUT_OF_STOCK", transaction_id=tx_id)
            cur.execute(
                """INSERT INTO marketplace_inventory_reservations
                (seller_transaction_id,buyer_user_id,listing_id,quantity,status,created_at,updated_at)
                VALUES (?,?,?,?, 'held',?,?) ON CONFLICT(seller_transaction_id) DO NOTHING""",
                (tx_id, buyer_id, listing_id, qty, now, now),
            )

        if cash_payment:
            return _json(marketplace_payment_pause.cash_checkout_payload(
                ok=True,
                transaction_id=tx_id,
                amount_cents=amount,
                currency=currency,
                seller_net_cents=commercial_quote["seller_earnings_minor"],
                commercial_quote=commercial_quote,
            ))

        try:
            base = (bot.APP_BASE_URL or request.url_root.rstrip("/")).rstrip("/")
            checkout_metadata = {"seller_transaction_id": str(tx_id), "offer_id": str(offer_id),
                                 "item_type": "marketplace_product", "item_id": str(listing_id),
                                 "buyer_user_id": str(buyer_id), "seller_user_id": str(seller_id),
                                 "listing_ids": str(listing_id), "quantities": str(qty),
                                 "fulfillment": fulfillment_kind}
            payment_intent_data = {"metadata": dict(checkout_metadata)}
            # The address the buyer typed on the review step, handed to Stripe
            # rather than re-requested from them a screen later.
            if stripe_shipping_object:
                payment_intent_data["shipping"] = stripe_shipping_object
            # The old gate here read the raw account id, which exists from the
            # moment onboarding *starts*. Stripe then rejects the transfer to an
            # account that cannot yet accept charges, and the buyer sees
            # "Checkout could not be created." The shared capability check routes
            # to Connect only when charges and payouts are both enabled, and
            # otherwise settles on the platform with the seller's share recorded
            # in seller_transactions — an unfinished seller onboarding is not a
            # buyer checkout prerequisite.
            connected_account_id = bot.seller_destination_account_id(payout)
            if connected_account_id:
                payment_intent_data.update({"application_fee_amount": platform_fee,
                                            "transfer_data": {"destination": connected_account_id}})
            payout_state = "connect_routed" if connected_account_id else "ledger_pending_onboarding"
            if native_sheet:
                # Server-authoritative amount: the accepted offer price times qty,
                # the same number the review screen was given. The sheet renders
                # it, it never supplies it.
                intent = bot.stripe.PaymentIntent.create(
                    amount=amount,
                    currency=currency.lower(),
                    automatic_payment_methods={"enabled": True},
                    metadata=checkout_metadata,
                    **{k: v for k, v in payment_intent_data.items() if k != "metadata"},
                    idempotency_key=f"marketplace-offer-sheet:{buyer_id}:{tx_id}",
                )
                intent_id = stripe_response_value(intent, "id")
                client_secret = stripe_response_value(intent, "client_secret")
                cur.execute(
                    "UPDATE seller_transactions SET stripe_payment_intent_id=?, status='checkout_created', updated_at=? WHERE id=?",
                    (intent_id, now, tx_id),
                )
                return _json({
                    "ok": True,
                    "payment_intent_client_secret": client_secret,
                    "payment_intent_id": intent_id,
                    "publishable_key": bot.STRIPE_PUBLISHABLE_KEY,
                    # The sheet header names the store, never the account holder.
                    "merchant_display_name": seller_identity.display_store_name(listing),
                    "apple_pay_merchant_id": _apple_pay_merchant_id(),
                    "amount_cents": amount,
                    "currency": currency,
                    "transaction_id": tx_id,
                    "platform_fee_cents": platform_fee,
                    "seller_net_cents": commercial_quote["seller_earnings_minor"],
                    "commercial_quote": commercial_quote,
                    "payout_state": payout_state,
                })
            session_obj = bot.stripe.checkout.Session.create(
                mode="payment",
                line_items=[{"price_data": {"currency": currency.lower(),
                                             "unit_amount": int(offer.get("amount_minor") or 0),
                                             "product_data": {"name": (listing.get("title") or "Marketplace item")[:120]}},
                             "quantity": qty}],
                success_url=f"{base}/pulse/payments/success?transaction_id={tx_id}",
                cancel_url=f"{base}/pulse/payments/cancel?transaction_id={tx_id}",
                payment_intent_data=payment_intent_data,
                metadata=checkout_metadata,
                idempotency_key=f"marketplace-offer:{buyer_id}:{tx_id}",
                # A pickup-only offer is never asked for a delivery address, and
                # neither is one whose address PulseSoc already collected.
                **({} if stripe_shipping_object
                   else stripe_shipping_checkout_params([fulfillment_kind])),
            )
            cur.execute(
                "UPDATE seller_transactions SET stripe_checkout_session_id=?, status='checkout_created', updated_at=? WHERE id=?",
                (session_obj.get("id"), now, tx_id),
            )
            return _json({"ok": True, "checkout_url": session_obj.get("url"),
                          "transaction_id": tx_id, "amount_cents": amount,
                          "platform_fee_cents": platform_fee,
                          "seller_net_cents": commercial_quote["seller_earnings_minor"],
                          "commercial_quote": commercial_quote,
                          "payout_state": payout_state})
        except Exception as exc:
            trace_id = secrets.token_hex(6)
            LOGGER.exception("OFFER_CHECKOUT_CREATE_FAILED trace_id=%s offer_id=%s", trace_id, offer_id)
            classified = classify_provider_exception(exc)
            release_inventory_reservation(cur, tx_id, now=now)
            cur.execute(
                "UPDATE seller_transactions SET status='checkout_failed', metadata_json=?, updated_at=? WHERE id=?",
                (json.dumps({"error": str(exc), "trace_id": trace_id,
                             "provider_error": classified["provider_error"]}, default=str), now, tx_id),
            )
            return _error(classified["message"], classified["status"],
                          code=classified["code"], trace_id=trace_id, transaction_id=tx_id,
                          provider_error=classified["provider_error"])

    return _with_db(handler)


def register(app) -> None:
    app.register_blueprint(offers_blueprint)
