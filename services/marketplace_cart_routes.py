"""Marketplace cart — the Phase 2 route pack.

The mobile client contract is `mobile-native/src/api/marketplaceCart.ts`
(created alongside this pack); the flag it flips is `MARKETPLACE_CART_ENABLED`
in `mobile-native/src/api/marketplaceOffers.ts`. Until this pack is deployed,
that flag must stay false — flipping it is a data change, not a UI change.

Design notes
------------

*Storage.* One row per (user, listing) in `marketplace_cart_items`, with the
price captured at add time (`price_snapshot_minor`). The snapshot is what makes
honest price-change handling possible: a line whose current listing price no
longer matches its snapshot is returned as `price_changed` and cannot be
checked out until the buyer confirms the new price (`POST /<line>/confirm-price`
re-snapshots). Silently charging the new price would be the dishonest path.

*Validation is computed, never stored.* Line state (available / price_changed /
sold / removed / restricted / low_stock) is derived from the listing row at
read time. There is no state column to go stale.

*Checkout.* One Stripe Checkout Session per seller group, reusing the exact
surface `/api/pulse/payments/checkout` uses: `seller_transactions` rows,
platform fee via `seller_fee_bps`, destination charge to the seller's connected
account. Stripe Connect allows one transfer destination per session, which is
why the group is per seller — this is a constraint, not a product choice.

*Idempotency.* `POST /checkout` accepts an `idempotency_key`. A replayed key
returns the stored response instead of creating a second session. Duplicate
add-taps are absorbed by the UNIQUE(user_id, listing_id) constraint: a second
add updates quantity rather than duplicating the line.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
from datetime import datetime, timezone

from flask import Blueprint, jsonify, request

from services import marketplace_fulfillment
from services import marketplace_listing_lifecycle as listing_lifecycle
from services import marketplace_seller_identity as seller_identity
from services.marketplace_payment_errors import (
    below_minimum_charge_error,
    classify_provider_exception,
    stripe_response_value,
)
from services import marketplace_quote_service
from services import marketplace_goods_policy

LOGGER = logging.getLogger(__name__)

cart_blueprint = Blueprint("pulse_marketplace_cart", __name__)

API_PREFIX = "/api/pulse/marketplace/cart"


def stripe_shipping_checkout_params(fulfillments) -> dict:
    """Collect a shipping address only when an order actually ships.

    Stripe requires an explicit country allowlist. Production may widen it via
    ``MARKETPLACE_SHIPPING_COUNTRIES``; the conservative default matches the
    currently launched US Marketplace instead of pretending global delivery.
    """
    if not {"shipping", "both"}.intersection({str(value or "").lower() for value in fulfillments}):
        return {}
    configured = [value.strip().upper() for value in os.getenv("MARKETPLACE_SHIPPING_COUNTRIES", "US").split(",")]
    allowed = [value for value in configured if len(value) == 2 and value.isalpha()]
    return {"shipping_address_collection": {"allowed_countries": allowed or ["US"]}}

MAX_QTY_PER_LINE = 20
MAX_LINES = 100

_SCHEMA_READY = False


def _bot():
    import bot

    return bot


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _json(payload, status: int = 200):
    response = jsonify(payload)
    response.headers["Cache-Control"] = "no-store, max-age=0, must-revalidate"
    return response, status


def _error(message: str, status: int = 400, *, code: str = "", **extra):
    """A rejection the buyer can act on.

    ``code`` is the stable, machine-readable half of the contract — the native
    client maps it to buyer-facing copy and never parses ``message``. Prose gets
    reworded; codes do not. The vocabulary is fixed and shared with the client:

        ITEM_UNAVAILABLE, OUT_OF_STOCK, SELLER_UNAVAILABLE, INVALID_QUANTITY,
        CART_FULL, PRICE_CHANGED, ADDRESS_REQUIRED, PAYMENT_UNAVAILABLE,
        PAYMENT_CONFIGURATION_ERROR, PAYMENT_FAILED, NETWORK_ERROR,
        ORDER_TOTAL_BELOW_MINIMUM, FULFILLMENT_REQUIRED, LOGIN_REQUIRED,
        NOT_FOUND

    ``message`` stays human because web and admin surfaces render it directly.
    """
    payload = {"ok": False, "message": message, **extra}
    if code:
        # Both spellings: `error_code` is what `pulseApi` reads, `error` is what
        # the older web handlers already look for.
        payload["error_code"] = code
        payload.setdefault("error", code)
    return _json(payload, status)


def _require_user():
    try:
        user = _bot().api_account_user()
    except Exception:
        LOGGER.exception("CART_AUTH_LOOKUP_FAILED")
        user = None
    if not user:
        return None, _error("Login required.", 401, code="LOGIN_REQUIRED")
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
        CREATE TABLE IF NOT EXISTS marketplace_cart_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            listing_id INTEGER,
            qty INTEGER DEFAULT 1,
            price_snapshot_minor INTEGER DEFAULT 0,
            currency TEXT DEFAULT 'USD',
            added_at TEXT,
            updated_at TEXT,
            UNIQUE(user_id, listing_id)
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS marketplace_cart_checkout_keys (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            idempotency_key TEXT,
            response_json TEXT,
            created_at TEXT,
            UNIQUE(user_id, idempotency_key)
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS marketplace_inventory_reservations (
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
    )
    _SCHEMA_READY = True


# --------------------------------------------------------------------------
# Line state
# --------------------------------------------------------------------------

def _listing_price_minor(bot, listing: dict) -> tuple[int, str]:
    amount, currency = bot.parse_price_label_to_cents(
        listing.get("price_label") or "", listing.get("currency") or "USD"
    )
    return int(amount or 0), currency or "USD"


def _line_state(line: dict, listing: dict, price_now_minor: int) -> str:
    """Derive the per-line state the client renders. Order matters: the states
    that block checkout hardest are reported first."""
    if not listing:
        return "removed"
    status = (listing.get("status") or "").lower()
    approval = (listing.get("approval_status") or "").lower()
    seller_status = (listing.get("seller_status") or "").lower()
    quantity = listing.get("quantity")
    if seller_status != "approved" or status not in listing_lifecycle.PUBLIC_STATUSES or approval not in listing_lifecycle.APPROVED_STATES:
        return "restricted"
    if not listing_lifecycle.inventory_available(listing, int(line.get("qty") or 1)):
        return "sold"
    if price_now_minor != int(line.get("price_snapshot_minor") or 0):
        return "price_changed"
    if quantity is not None and int(quantity) < int(line.get("qty") or 1):
        return "low_stock"
    return "available"


def _fulfillment(listing: dict) -> str:
    delivery = (listing.get("delivery_type") or "").lower()
    if delivery in {"digital", "download"}:
        return "digital"
    if delivery in {"pickup", "local", "meetup"}:
        return "pickup"
    # A seller who offers both is reported as ``both`` rather than being silently
    # resolved to shipping. Collapsing it here is how a buyer ends up entering a
    # delivery address for an item they intended to collect in person — the
    # choice is theirs to make, so it has to survive to the checkout screen.
    if delivery in {"both", "pickup_or_shipping", "shipping_or_pickup"}:
        return "both"
    return "shipping"


def _listing_metadata(listing: dict) -> dict:
    try:
        meta = json.loads(listing.get("listing_metadata_json") or "{}")
    except Exception:
        return {}
    return meta if isinstance(meta, dict) else {}


def _fulfillment_kind(listing: dict) -> str:
    return marketplace_fulfillment.resolve_kind(
        listing.get("listing_type") or listing.get("product_type"),
        listing.get("delivery_type"),
        _listing_metadata(listing),
    )


def _apple_pay_merchant_id() -> str:
    """Empty means the native sheet offers card only.

    Apple Pay needs a merchant identifier that matches an entitlement in the
    signed binary; announcing one the app cannot honour makes the sheet fail at
    presentation rather than fall back, so an unset value is returned as-is and
    the client simply does not request Apple Pay.
    """
    import os

    return str(os.getenv("APPLE_PAY_MERCHANT_ID") or "").strip()


def _stripe_payment_intent_data(*, bot, tx_ids: list[int], buyer_id: int,
                                platform_fee: int, payout: dict) -> tuple[dict, str]:
    """Build a platform charge by default, upgrading to a destination charge
    only when the seller's Connect account is one Stripe will actually accept a
    transfer to. Seller earnings are recorded in ``seller_transactions`` either
    way, so an unfinished onboarding never blocks the buyer from paying."""
    data = {"metadata": {
        "seller_transaction_ids": ",".join(str(value) for value in tx_ids),
        "cart_checkout": "1",
        "buyer_user_id": str(buyer_id),
    }}
    connected_account_id = bot.seller_destination_account_id(payout)
    if connected_account_id:
        data.update({
            "application_fee_amount": int(platform_fee),
            "transfer_data": {"destination": connected_account_id},
        })
    return data, connected_account_id


def _serialize_lines(bot, cur, user_id: int) -> list[dict]:
    # The cart names the *store* the buyer is buying from, exactly as the product
    # page did. `users` is not joined at all here: with no personal name in the
    # result set there is nothing for a later edit to fall back to by accident.
    cur.execute(
        f"""
        SELECT c.id AS line_id, c.listing_id, c.qty, c.price_snapshot_minor,
               c.currency AS snapshot_currency, c.added_at,
               l.id AS l_id, l.seller_user_id, l.title, l.price_label,
               l.currency, l.quantity, l.status, l.approval_status,
               l.delivery_type, l.product_type, l.listing_type, l.cover_image_url,
               l.category, l.subcategory, l.description, l.listing_metadata_json,
               COALESCE(ms.status, 'missing') AS seller_status,
               {seller_identity.store_name_select('ms')}
        FROM marketplace_cart_items c
        LEFT JOIN marketplace_listings l ON l.id = c.listing_id
        LEFT JOIN marketplace_sellers ms ON ms.user_id = l.seller_user_id
        WHERE c.user_id = ?
        ORDER BY c.added_at DESC
        """,
        (user_id,),
    )
    lines = []
    for row in cur.fetchall():
        row = dict(row)
        listing = {k: row.get(k) for k in (
            "seller_user_id", "title", "price_label", "currency", "quantity",
            "status", "approval_status", "seller_status", "delivery_type", "product_type", "listing_type", "cover_image_url",
            "category", "subcategory", "description", "listing_metadata_json",
        )} if row.get("l_id") else {}
        price_now, currency_now = (_listing_price_minor(bot, listing)
                                   if listing else (0, row.get("snapshot_currency") or "USD"))
        state = _line_state(row, listing, price_now)
        lines.append({
            "line_id": int(row["line_id"]),
            "listing_id": int(row["listing_id"] or 0),
            "qty": int(row["qty"] or 1),
            "state": state,
            "price_snapshot_minor": int(row["price_snapshot_minor"] or 0),
            "price_now_minor": price_now,
            "currency": currency_now,
            "title": listing.get("title") or "Removed listing",
            "cover_image_url": listing.get("cover_image_url") or "",
            # Routing identity and presented identity, side by side and never
            # conflated: `seller_user_id` addresses the account (DMs, payouts),
            # `seller_store_name` is the only thing the buyer reads. `seller_name`
            # stays as an alias of the same value so older consumers keep working
            # without drifting back to the account holder's personal name.
            "seller_user_id": int(listing.get("seller_user_id") or 0),
            "seller_store_name": seller_identity.display_store_name(row),
            "seller_name": seller_identity.display_store_name(row),
            "fulfillment": _fulfillment(listing) if listing else "shipping",
            # The canonical kind, which distinguishes the things `fulfillment`
            # cannot: a booking from a parcel, a remote service from an on-site
            # one. It decides what the buyer is asked for before paying.
            "fulfillment_kind": _fulfillment_kind(listing) if listing else "shipping",
            "listing_metadata": _listing_metadata(listing) if listing else {},
            "goods_policy": marketplace_goods_policy.evaluate(listing) if listing else {},
            "added_at": row.get("added_at") or "",
        })
    return lines


# --------------------------------------------------------------------------
# Routes
# --------------------------------------------------------------------------

@cart_blueprint.route(API_PREFIX, methods=["GET"])
def cart_list():
    bot = _bot()
    bot.init_db()
    user, err = _require_user()
    if err:
        return err

    def handler(cur, conn):
        lines = _serialize_lines(bot, cur, int(user["user_id"]))
        checkoutable = [l for l in lines if l["state"] == "available"]
        return _json({
            "ok": True,
            "lines": lines,
            "badge_count": sum(l["qty"] for l in lines if l["state"] in {"available", "price_changed", "low_stock"}),
            "checkoutable_count": len(checkoutable),
        })

    return _with_db(handler)


@cart_blueprint.route(API_PREFIX, methods=["POST"])
def cart_add():
    bot = _bot()
    bot.init_db()
    user, err = _require_user()
    if err:
        return err
    payload = request.get_json(silent=True) or {}
    listing_id = int(payload.get("listing_id") or 0)
    qty = max(1, min(int(payload.get("qty") or 1), MAX_QTY_PER_LINE))
    if not listing_id:
        return _error("Choose an item to add.", 400, code="ITEM_UNAVAILABLE")

    def handler(cur, conn):
        cur.execute("SELECT * FROM marketplace_listings WHERE id=? LIMIT 1", (listing_id,))
        listing = dict(cur.fetchone() or {})
        if not listing:
            return _error("Listing not found.", 404, code="ITEM_UNAVAILABLE")
        if int(listing.get("seller_user_id") or 0) == int(user["user_id"]):
            return _error("You cannot add your own listing.", 400, code="OWN_LISTING")
        seller = bot.approved_marketplace_seller_for_user(cur, listing.get("seller_user_id"))
        listing["seller_status"] = (seller or {}).get("status") or ""
        # Project the store name onto the listing explicitly rather than relying
        # on `SELECT *` to have carried a same-named column: publication depends
        # on the seller having a public store identity, and that judgement must
        # be made from the seller record it actually lives on.
        listing["seller_store_name"] = seller_identity.store_name(seller)
        # One helper names *why* the listing is not purchasable, so a suspended
        # seller, a withdrawn listing, and an empty shelf stay distinguishable
        # all the way to the buyer's screen.
        denial = listing_lifecycle.public_denial_code(listing, qty)
        if denial:
            return _error({
                "SELLER_UNAVAILABLE": "This seller is not accepting orders right now.",
                "OUT_OF_STOCK": "This item is out of stock.",
            }.get(denial, "This listing is no longer available."), 409, code=denial)
        price_minor, currency = _listing_price_minor(bot, listing)
        if price_minor <= 0:
            return _error("This item is not priced for checkout.", 400, code="ITEM_UNAVAILABLE")
        cur.execute(
            "SELECT COUNT(*) AS n FROM marketplace_cart_items WHERE user_id=?",
            (int(user["user_id"]),),
        )
        if int(dict(cur.fetchone() or {}).get("n") or 0) >= MAX_LINES:
            return _error("Cart is full.", 409, code="CART_FULL")
        now = _now()
        # A duplicate tap must not duplicate the line: the UNIQUE constraint
        # turns the second add into a quantity update.
        #
        # The clamp is a CASE expression, not `MIN(qty + excluded.qty, N)`.
        # `MIN(a, b)` is a SQLite-only scalar — PostgreSQL's `min()` is a
        # one-argument aggregate (the scalar is `LEAST`), and aggregates are
        # illegal inside DO UPDATE SET regardless. Postgres plans the whole
        # statement up front, so that form failed on the *first* add rather than
        # only on conflict: add-to-cart passed on local SQLite and returned a
        # 500 in production. `services/db.py` has no MIN→LEAST rewrite, so the
        # portable CASE form is what keeps one statement correct on both.
        cur.execute(
            """
            INSERT INTO marketplace_cart_items
                (user_id, listing_id, qty, price_snapshot_minor, currency, added_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id, listing_id)
            DO UPDATE SET
                qty=CASE WHEN marketplace_cart_items.qty + excluded.qty > ?
                         THEN ?
                         ELSE marketplace_cart_items.qty + excluded.qty END,
                price_snapshot_minor=excluded.price_snapshot_minor,
                currency=excluded.currency,
                updated_at=excluded.updated_at
            """,
            (
                int(user["user_id"]), listing_id, qty, price_minor, currency, now, now,
                MAX_QTY_PER_LINE, MAX_QTY_PER_LINE,
            ),
        )
        lines = _serialize_lines(bot, cur, int(user["user_id"]))
        return _json({"ok": True, "lines": lines,
                      "badge_count": sum(l["qty"] for l in lines if l["state"] in {"available", "price_changed", "low_stock"})})

    return _with_db(handler)


@cart_blueprint.route(f"{API_PREFIX}/<int:line_id>", methods=["PATCH", "POST"])
def cart_update(line_id: int):
    bot = _bot()
    bot.init_db()
    user, err = _require_user()
    if err:
        return err
    payload = request.get_json(silent=True) or {}
    qty = max(1, min(int(payload.get("qty") or 1), MAX_QTY_PER_LINE))

    def handler(cur, conn):
        cur.execute(
            "UPDATE marketplace_cart_items SET qty=?, updated_at=? WHERE id=? AND user_id=?",
            (qty, _now(), line_id, int(user["user_id"])),
        )
        if not cur.rowcount:
            return _error("Cart line not found.", 404, code="NOT_FOUND")
        return _json({"ok": True, "line_id": line_id, "qty": qty})

    return _with_db(handler)


@cart_blueprint.route(f"{API_PREFIX}/<int:line_id>", methods=["DELETE"])
def cart_remove(line_id: int):
    bot = _bot()
    bot.init_db()
    user, err = _require_user()
    if err:
        return err

    def handler(cur, conn):
        cur.execute(
            "DELETE FROM marketplace_cart_items WHERE id=? AND user_id=?",
            (line_id, int(user["user_id"])),
        )
        if not cur.rowcount:
            return _error("Cart line not found.", 404, code="NOT_FOUND")
        return _json({"ok": True, "line_id": line_id})

    return _with_db(handler)


@cart_blueprint.route(f"{API_PREFIX}/<int:line_id>/confirm-price", methods=["POST"])
def cart_confirm_price(line_id: int):
    """The buyer has seen the new price and accepted it: re-snapshot."""
    bot = _bot()
    bot.init_db()
    user, err = _require_user()
    if err:
        return err

    def handler(cur, conn):
        cur.execute(
            """
            SELECT c.id, c.listing_id, l.price_label, l.currency
            FROM marketplace_cart_items c
            LEFT JOIN marketplace_listings l ON l.id = c.listing_id
            WHERE c.id=? AND c.user_id=? LIMIT 1
            """,
            (line_id, int(user["user_id"])),
        )
        row = dict(cur.fetchone() or {})
        if not row:
            return _error("Cart line not found.", 404, code="NOT_FOUND")
        price_minor, currency = _listing_price_minor(bot, row)
        if price_minor <= 0:
            return _error("This item is no longer priced for checkout.", 409, code="ITEM_UNAVAILABLE")
        cur.execute(
            "UPDATE marketplace_cart_items SET price_snapshot_minor=?, currency=?, updated_at=? WHERE id=?",
            (price_minor, currency, _now(), line_id),
        )
        return _json({"ok": True, "line_id": line_id, "price_snapshot_minor": price_minor, "currency": currency})

    return _with_db(handler)


@cart_blueprint.route(f"{API_PREFIX}/validate", methods=["POST"])
def cart_validate():
    bot = _bot()
    bot.init_db()
    user, err = _require_user()
    if err:
        return err

    def handler(cur, conn):
        lines = _serialize_lines(bot, cur, int(user["user_id"]))
        blocking = [l for l in lines if l["state"] in {"sold", "removed", "restricted"}
                    or l.get("goods_policy", {}).get("decision") != "ALLOWED"]
        needs_confirmation = [l for l in lines if l["state"] == "price_changed"]
        return _json({
            "ok": True,
            "lines": lines,
            "can_checkout": bool(lines) and not blocking and not needs_confirmation,
            "blocking_line_ids": [l["line_id"] for l in blocking],
            "price_changed_line_ids": [l["line_id"] for l in needs_confirmation],
        })

    return _with_db(handler)


@cart_blueprint.route(f"{API_PREFIX}/checkout", methods=["POST"])
def cart_checkout():
    """Check out one seller's group of available lines as a single Stripe
    session. Sold / removed / restricted lines block; price-changed lines block
    until confirmed. Reuses the seller_transactions surface line-for-line."""
    bot = _bot()
    bot.init_db()
    user, err = _require_user()
    if err:
        return err
    payload = request.get_json(silent=True) or {}
    seller_user_id = int(payload.get("seller_user_id") or 0)
    idempotency_key = str(payload.get("idempotency_key") or "").strip()[:120]
    # How the buyer chose to receive the order, when the seller offers a choice.
    # It decides whether Stripe asks for a delivery address, so it has to arrive
    # with the checkout request rather than be inferred afterwards.
    fulfillment_choice = str(payload.get("fulfillment") or "").strip().lower()
    # `payment_sheet` keeps the buyer inside the app: the same validated group is
    # settled with a PaymentIntent the native Stripe sheet can present, instead
    # of a hosted Session the phone has to open in Safari. Everything before the
    # provider call — eligibility, price authority, inventory reservation — is
    # deliberately shared, so the two surfaces cannot drift into two policies.
    native_sheet = str(payload.get("payment_mode") or "").strip().lower() == "payment_sheet"
    if not seller_user_id:
        return _error("Choose a seller group to check out.", 400, code="INVALID_REQUEST")

    def handler(cur, conn):
        buyer_id = int(user["user_id"])
        if idempotency_key:
            cur.execute(
                "SELECT response_json FROM marketplace_cart_checkout_keys WHERE user_id=? AND idempotency_key=? LIMIT 1",
                (buyer_id, idempotency_key),
            )
            stored = dict(cur.fetchone() or {})
            if stored.get("response_json"):
                return _json({**json.loads(stored["response_json"]), "replayed": True})

        lines = [l for l in _serialize_lines(bot, cur, buyer_id) if l["seller_user_id"] == seller_user_id]
        if not lines:
            return _error("No items from this seller in your cart.", 404, code="NOT_FOUND")
        if bot.ios_native_app_request() and any(l["fulfillment"] == "digital" for l in lines):
            return bot.ios_paid_digital_unavailable_response(api=True)
        blocking = [l for l in lines if l["state"] in {"sold", "removed", "restricted"}
                    or l.get("goods_policy", {}).get("decision") != "ALLOWED"]
        if blocking:
            return _error(
                "Some items are no longer available.", 409,
                code="OUT_OF_STOCK" if all(l["state"] == "sold" for l in blocking) else "ITEM_UNAVAILABLE",
                blocking_line_ids=[l["line_id"] for l in blocking],
            )
        unconfirmed = [l for l in lines if l["state"] == "price_changed"]
        if unconfirmed:
            return _error("Prices changed since you added these items. Confirm the new prices first.", 409,
                          code="PRICE_CHANGED",
                          price_changed_line_ids=[l["line_id"] for l in unconfirmed])

        currency = lines[0]["currency"]
        if any(l["currency"] != currency for l in lines):
            return _error("Items in different currencies must be checked out separately.", 409,
                          code="MIXED_CURRENCY")

        # When the seller offers pickup *or* shipping, the buyer picks — the
        # server does not guess. The choice decides whether Stripe collects a
        # delivery address, so it is resolved here, before any money surface
        # exists, and refused rather than defaulted if it is missing.
        if any(l["fulfillment"] == "both" for l in lines) and fulfillment_choice not in {"pickup", "shipping"}:
            return _error("Choose pickup or delivery before you pay.", 400,
                          code="FULFILLMENT_REQUIRED")
        resolved_lanes = [
            fulfillment_choice if l["fulfillment"] == "both" else l["fulfillment"]
            for l in lines
        ]

        # A group checkout settles every line against one set of buyer details,
        # which is right for an address — a cart ships to one place — and wrong
        # for a date: two bookings in one basket need two slots, and there is
        # nowhere in a shared form to put the second. Those are refused here and
        # bought one at a time, rather than charged with the question unasked.
        # The guard above only settles the physical both-lanes case; a service
        # the seller offers remotely *or* on site is equally undecided and its
        # answer changes whether an address is asked for at all.
        line_kinds = []
        for line in lines:
            kind, lane_error = marketplace_fulfillment.resolve_choice(
                line.get("fulfillment_kind") or "shipping", fulfillment_choice
            )
            if lane_error:
                return _error("Choose how you want this order fulfilled before you pay.", 400, code=lane_error)
            line_kinds.append(kind)
        scheduled = [k for k in line_kinds if k.startswith(("service_", "booking_", "event_"))]
        if scheduled and len(lines) > 1:
            return _error(
                "Bookings, services and events are checked out one at a time. Buy this item on its own.",
                409, code="ITEM_NEEDS_OWN_CHECKOUT")
        # One address for the group, asked for only when something in it travels.
        details_kind = next((k for k in line_kinds if marketplace_fulfillment.needs_shipping_address(k)), "")
        group_details: dict = {}
        stripe_shipping_object: dict = {}
        if not details_kind and scheduled:
            details_kind = scheduled[0]
        if not details_kind:
            details_kind = next((k for k in line_kinds if k == "pickup"), "")
        if details_kind:
            details_ok, group_details = marketplace_fulfillment.validate_details(
                details_kind, payload.get("fulfillment_details"),
                lines[0].get("listing_metadata") if len(lines) == 1 else {},
            )
            if not details_ok:
                return _error(group_details["message"], group_details["status"],
                              code=group_details["code"], field=group_details.get("field", ""))
            stripe_shipping_object = marketplace_fulfillment.stripe_shipping(group_details)
        fulfillment_snapshot = (
            marketplace_fulfillment.snapshot(details_kind, group_details) if details_kind else {}
        )

        approved = bot.approved_marketplace_seller_for_user(cur, seller_user_id)
        if not approved:
            # Seller *approval* is a marketplace-eligibility gate and is a real
            # reason to stop. Seller *Connect onboarding* is not: that routes to
            # a platform charge below rather than blocking the buyer.
            return _error("This seller is not accepting orders right now.", 403, code="SELLER_UNAVAILABLE")
        payout = bot.seller_payout_account(cur, seller_user_id, "merchant")
        fee_bps = bot.seller_fee_bps(cur, "merchant")

        now = _now()
        line_quotes = [marketplace_quote_service.create_quote(
            listing_id=l["listing_id"], seller_id=seller_user_id,
            quantity=l["qty"], unit_price_minor=l["price_snapshot_minor"],
            currency=currency, live_fee_bps=fee_bps,
            shipping={"fulfillment": lane},
        ) for l, lane in zip(lines, resolved_lanes)]
        total_minor = sum(q["buyer_total_minor"] for q in line_quotes)
        platform_fee = sum(q["platform_fee_minor"] for q in line_quotes)
        seller_net = sum(q["seller_earnings_minor"] for q in line_quotes)

        # The cart settles as one charge, so it is the group total that has to
        # clear the per-currency floor — a 10c line is fine next to a $9 one.
        below_minimum = below_minimum_charge_error(total_minor, currency)
        if below_minimum:
            return _error(below_minimum["message"], below_minimum["status"],
                          code=below_minimum["code"], total_cents=total_minor,
                          minimum_charge_cents=below_minimum["minimum_minor"])

        tx_ids = []
        for l, commercial_quote in zip(lines, line_quotes):
            line_amount = commercial_quote["buyer_total_minor"]
            line_fee = commercial_quote["platform_fee_minor"]
            cur.execute(
                """
                INSERT INTO seller_transactions
                (buyer_user_id, seller_user_id, seller_type, item_type, item_id, amount_cents, currency,
                 platform_fee_cents, seller_net_cents, status, metadata_json, created_at, updated_at)
                VALUES (?, ?, 'merchant', 'marketplace_product', ?, ?, ?, ?, ?, 'created', ?, ?, ?)
                """,
                (buyer_id, seller_user_id, l["listing_id"], line_amount, currency,
                 line_fee, commercial_quote["seller_earnings_minor"],
                 marketplace_quote_service.transaction_metadata(
                     {"title": l["title"], "qty": l["qty"], "cart_line_id": l["line_id"],
                      **({"fulfillment": fulfillment_snapshot} if fulfillment_snapshot else {})},
                     commercial_quote, payout_state="pending_checkout"),
                 now, now),
            )
            tx_ids.append(int(cur.lastrowid))

        if not bot.STRIPE_SECRET_KEY:
            for tx_id in tx_ids:
                cur.execute("UPDATE seller_transactions SET status='blocked_stripe_not_configured', updated_at=? WHERE id=?", (now, tx_id))
            return _error("Stripe checkout is not configured yet. No card was charged.", 503,
                          code="PAYMENT_UNAVAILABLE", transaction_ids=tx_ids)
        # Reserve physical inventory before handing the buyer to Stripe. The
        # reservation is keyed to the transaction, so duplicate taps cannot
        # decrement twice; expiry/failure restores it.
        # Stock is held for things there is a finite number of. A booking or a
        # seat at an event has no parcel to run out of, and the legacy test here
        # only recognised `digital` — so those lines decremented a quantity that
        # means nothing to them, in the one lane that still did it.
        for line, tx_id, line_kind in zip(lines, tx_ids, line_kinds):
            if line_kind in marketplace_fulfillment.STOCKLESS_KINDS:
                continue
            cur.execute(
                "UPDATE marketplace_listings SET quantity=quantity-?, updated_at=? "
                "WHERE id=? AND quantity>=?",
                (line["qty"], now, line["listing_id"], line["qty"]),
            )
            if not cur.rowcount:
                for held_tx in tx_ids:
                    release_inventory_reservation(cur, held_tx, now=now)
                return _error("An item sold out before checkout. No card was charged.", 409,
                              code="OUT_OF_STOCK")
            cur.execute(
                """INSERT INTO marketplace_inventory_reservations
                (seller_transaction_id,buyer_user_id,listing_id,quantity,status,created_at,updated_at)
                VALUES (?,?,?,?, 'held',?,?) ON CONFLICT(seller_transaction_id) DO NOTHING""",
                (tx_id, buyer_id, line["listing_id"], line["qty"], now, now),
            )

        try:
            base = (bot.APP_BASE_URL or request.url_root.rstrip("/")).rstrip("/")
            primary_tx = tx_ids[0]
            payment_intent_data, connected_account_id = _stripe_payment_intent_data(
                bot=bot, tx_ids=tx_ids, buyer_id=buyer_id, platform_fee=platform_fee, payout=payout
            )
            # The address the buyer typed on the review step, handed to Stripe
            # rather than re-requested from them a screen later.
            if stripe_shipping_object:
                payment_intent_data["shipping"] = stripe_shipping_object
            checkout_metadata = {
                "seller_transaction_ids": ",".join(str(t) for t in tx_ids),
                "cart_checkout": "1",
                "buyer_user_id": str(buyer_id),
                "seller_user_id": str(seller_user_id),
                "cart_line_ids": ",".join(str(l["line_id"]) for l in lines),
                "listing_ids": ",".join(str(l["listing_id"]) for l in lines),
                "quantities": ",".join(str(l["qty"]) for l in lines),
                "fulfillment": ",".join(resolved_lanes),
                "idempotency_key": idempotency_key,
            }
            if native_sheet:
                # The amount is the server's, computed from the same snapshot the
                # buyer was shown. The sheet renders it; it never supplies it.
                intent = bot.stripe.PaymentIntent.create(
                    amount=total_minor,
                    currency=currency.lower(),
                    automatic_payment_methods={"enabled": True},
                    metadata=checkout_metadata,
                    **{k: v for k, v in payment_intent_data.items() if k != "metadata"},
                    idempotency_key=f"marketplace-cart-sheet:{buyer_id}:{idempotency_key or primary_tx}",
                )
                intent_id = stripe_response_value(intent, "id")
                client_secret = stripe_response_value(intent, "client_secret")
                for tx_id in tx_ids:
                    cur.execute(
                        "UPDATE seller_transactions SET stripe_payment_intent_id=?, status='checkout_created', updated_at=? WHERE id=?",
                        (intent_id, now, tx_id),
                    )
                response_payload = {
                    "ok": True,
                    "payment_intent_client_secret": client_secret,
                    "payment_intent_id": intent_id,
                    "publishable_key": bot.STRIPE_PUBLISHABLE_KEY,
                    # The sheet header names the store, never the account holder.
                    "merchant_display_name": seller_identity.display_store_name(lines[0]),
                    "apple_pay_merchant_id": _apple_pay_merchant_id(),
                    # Echoed so the sheet cannot be presented against a number the
                    # review screen never showed.
                    "amount_cents": total_minor,
                    "currency": currency,
                    "transaction_ids": tx_ids,
                    "platform_fee_cents": platform_fee,
                    "seller_net_cents": seller_net,
                    "commercial_quotes": line_quotes,
                    "payout_state": "connect_routed" if connected_account_id else "ledger_pending_onboarding",
                }
                if idempotency_key:
                    cur.execute(
                        """
                        INSERT INTO marketplace_cart_checkout_keys (user_id, idempotency_key, response_json, created_at)
                        VALUES (?, ?, ?, ?)
                        ON CONFLICT(user_id, idempotency_key) DO NOTHING
                        """,
                        (buyer_id, idempotency_key, json.dumps(response_payload, default=str), now),
                    )
                return _json(response_payload)
            session_obj = bot.stripe.checkout.Session.create(
                mode="payment",
                line_items=[
                    {"price_data": {"currency": currency.lower(),
                                     "unit_amount": l["price_snapshot_minor"],
                                     "product_data": {"name": (l["title"] or "Marketplace item")[:120]}},
                     "quantity": l["qty"]}
                    for l in lines
                ],
                success_url=f"{base}/pulse/payments/success?transaction_id={primary_tx}",
                cancel_url=f"{base}/pulse/payments/cancel?transaction_id={primary_tx}",
                payment_intent_data=payment_intent_data,
                metadata=checkout_metadata,
                idempotency_key=f"marketplace-cart:{buyer_id}:{idempotency_key or primary_tx}",
                # Resolved lanes, not raw ones: a buyer who chose pickup is never
                # asked for a delivery address.
                # Stripe is only asked for an address PulseSoc does not already
                # hold — which, once the review step runs, is none of them.
                **({} if stripe_shipping_object else stripe_shipping_checkout_params(resolved_lanes)),
            )
            for tx_id in tx_ids:
                cur.execute(
                    "UPDATE seller_transactions SET stripe_checkout_session_id=?, status='checkout_created', updated_at=? WHERE id=?",
                    (session_obj.get("id"), now, tx_id),
                )
            # Lines leave the cart only after payment confirmation (webhook),
            # not here — an abandoned session must not empty the cart.
            response_payload = {
                "ok": True,
                "checkout_url": session_obj.get("url"),
                "transaction_ids": tx_ids,
                "total_cents": total_minor,
                "platform_fee_cents": platform_fee,
                "seller_net_cents": seller_net,
                "commercial_quotes": line_quotes,
                "payout_state": "connect_routed" if connected_account_id else "ledger_pending_onboarding",
            }
            if idempotency_key:
                cur.execute(
                    """
                    INSERT INTO marketplace_cart_checkout_keys (user_id, idempotency_key, response_json, created_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(user_id, idempotency_key) DO NOTHING
                    """,
                    (buyer_id, idempotency_key, json.dumps(response_payload, default=str), now),
                )
            return _json(response_payload)
        except Exception as exc:
            trace_id = secrets.token_hex(6)
            LOGGER.exception("CART_CHECKOUT_CREATE_FAILED trace_id=%s", trace_id)
            # Collapse the opaque catch-all into a canonical, self-diagnosing
            # error: the buyer gets copy matched to the actual failure class and
            # the owner sees the provider fingerprint (type/code/param) on the
            # response itself, not only in a Railway log.
            classified = classify_provider_exception(exc)
            for tx_id in tx_ids:
                release_inventory_reservation(cur, tx_id, now=now)
                cur.execute(
                    "UPDATE seller_transactions SET status='checkout_failed', metadata_json=?, updated_at=? WHERE id=?",
                    (json.dumps({"error": str(exc), "trace_id": trace_id,
                                 "provider_error": classified["provider_error"]}, default=str), now, tx_id),
                )
            return _error(classified["message"], classified["status"],
                          code=classified["code"], trace_id=trace_id, transaction_ids=tx_ids,
                          provider_error=classified["provider_error"])

    return _with_db(handler)


def capture_inventory_reservation(cur, seller_transaction_id: int, *, now: str | None = None) -> None:
    cur.execute("UPDATE marketplace_inventory_reservations SET status='captured', updated_at=? WHERE seller_transaction_id=? AND status='held'",
                (now or _now(), int(seller_transaction_id)))


def release_inventory_reservation(cur, seller_transaction_id: int, *, now: str | None = None) -> None:
    cur.execute("SELECT listing_id,quantity FROM marketplace_inventory_reservations WHERE seller_transaction_id=? AND status='held' LIMIT 1",
                (int(seller_transaction_id),))
    held = dict(cur.fetchone() or {})
    if not held:
        return
    cur.execute("UPDATE marketplace_listings SET quantity=COALESCE(quantity,0)+?, updated_at=? WHERE id=?",
                (int(held.get("quantity") or 0), now or _now(), int(held.get("listing_id") or 0)))
    cur.execute("UPDATE marketplace_inventory_reservations SET status='released', updated_at=? WHERE seller_transaction_id=? AND status='held'",
                (now or _now(), int(seller_transaction_id)))


def register(app) -> None:
    app.register_blueprint(cart_blueprint)
