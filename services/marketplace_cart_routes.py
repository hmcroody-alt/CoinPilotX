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
import secrets
from datetime import datetime, timezone

from flask import Blueprint, jsonify, request

LOGGER = logging.getLogger(__name__)

cart_blueprint = Blueprint("pulse_marketplace_cart", __name__)

API_PREFIX = "/api/pulse/marketplace/cart"

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


def _error(message: str, status: int = 400, **extra):
    return _json({"ok": False, "message": message, **extra}, status)


def _require_user():
    try:
        user = _bot().api_account_user()
    except Exception:
        LOGGER.exception("CART_AUTH_LOOKUP_FAILED")
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
    approval = (listing.get("approval_status") or "approved").lower()
    quantity = listing.get("quantity")
    if status not in {"active", "approved"}:
        return "sold"
    if approval not in {"approved", "review_ready", ""}:
        return "restricted"
    if quantity is not None and int(quantity or 0) <= 0:
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
    return "shipping"


def _serialize_lines(bot, cur, user_id: int) -> list[dict]:
    cur.execute(
        """
        SELECT c.id AS line_id, c.listing_id, c.qty, c.price_snapshot_minor,
               c.currency AS snapshot_currency, c.added_at,
               l.id AS l_id, l.seller_user_id, l.title, l.price_label,
               l.currency, l.quantity, l.status, l.approval_status,
               l.delivery_type, l.cover_image_url,
               COALESCE(u.display_name, u.username, 'PulseSoc Seller') AS seller_name
        FROM marketplace_cart_items c
        LEFT JOIN marketplace_listings l ON l.id = c.listing_id
        LEFT JOIN users u ON u.user_id = l.seller_user_id
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
            "status", "approval_status", "delivery_type", "cover_image_url",
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
            "seller_user_id": int(listing.get("seller_user_id") or 0),
            "seller_name": row.get("seller_name") or "PulseSoc Seller",
            "fulfillment": _fulfillment(listing) if listing else "shipping",
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
        return _error("Choose an item to add.", 400)

    def handler(cur, conn):
        cur.execute("SELECT * FROM marketplace_listings WHERE id=? LIMIT 1", (listing_id,))
        listing = dict(cur.fetchone() or {})
        if not listing:
            return _error("Listing not found.", 404)
        if int(listing.get("seller_user_id") or 0) == int(user["user_id"]):
            return _error("You cannot add your own listing.", 400)
        if (listing.get("status") or "").lower() not in {"active", "approved"}:
            return _error("This listing is no longer available.", 409)
        price_minor, currency = _listing_price_minor(bot, listing)
        if price_minor <= 0:
            return _error("This item is not priced for checkout.", 400)
        cur.execute(
            "SELECT COUNT(*) AS n FROM marketplace_cart_items WHERE user_id=?",
            (int(user["user_id"]),),
        )
        if int(dict(cur.fetchone() or {}).get("n") or 0) >= MAX_LINES:
            return _error("Cart is full.", 409)
        now = _now()
        # A duplicate tap must not duplicate the line: the UNIQUE constraint
        # turns the second add into a quantity update.
        cur.execute(
            """
            INSERT INTO marketplace_cart_items
                (user_id, listing_id, qty, price_snapshot_minor, currency, added_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id, listing_id)
            DO UPDATE SET qty=MIN(qty + excluded.qty, %d), updated_at=excluded.updated_at
            """ % MAX_QTY_PER_LINE,
            (int(user["user_id"]), listing_id, qty, price_minor, currency, now, now),
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
            return _error("Cart line not found.", 404)
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
            return _error("Cart line not found.", 404)
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
            return _error("Cart line not found.", 404)
        price_minor, currency = _listing_price_minor(bot, row)
        if price_minor <= 0:
            return _error("This item is no longer priced for checkout.", 409)
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
        blocking = [l for l in lines if l["state"] in {"sold", "removed", "restricted"}]
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
    if bot.ios_native_app_request():
        return bot.ios_paid_digital_unavailable_response(api=True)
    payload = request.get_json(silent=True) or {}
    seller_user_id = int(payload.get("seller_user_id") or 0)
    idempotency_key = str(payload.get("idempotency_key") or "").strip()[:120]
    if not seller_user_id:
        return _error("Choose a seller group to check out.", 400)

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
            return _error("No items from this seller in your cart.", 404)
        blocking = [l for l in lines if l["state"] in {"sold", "removed", "restricted"}]
        if blocking:
            return _error("Some items are no longer available.", 409,
                          blocking_line_ids=[l["line_id"] for l in blocking])
        unconfirmed = [l for l in lines if l["state"] == "price_changed"]
        if unconfirmed:
            return _error("Prices changed since you added these items. Confirm the new prices first.", 409,
                          price_changed_line_ids=[l["line_id"] for l in unconfirmed])

        currency = lines[0]["currency"]
        if any(l["currency"] != currency for l in lines):
            return _error("Items in different currencies must be checked out separately.", 409)

        approved = bot.approved_marketplace_seller_for_user(cur, seller_user_id)
        if not approved:
            return _error("Seller is not approved for payments.", 403)
        payout = bot.seller_payout_account(cur, seller_user_id, "merchant")
        fee_bps = bot.seller_fee_bps(cur, "merchant")

        now = _now()
        total_minor = sum(l["price_snapshot_minor"] * l["qty"] for l in lines)
        platform_fee = int(round(total_minor * fee_bps / 10000))
        seller_net = total_minor - platform_fee

        tx_ids = []
        for l in lines:
            line_amount = l["price_snapshot_minor"] * l["qty"]
            line_fee = int(round(line_amount * fee_bps / 10000))
            cur.execute(
                """
                INSERT INTO seller_transactions
                (buyer_user_id, seller_user_id, seller_type, item_type, item_id, amount_cents, currency,
                 platform_fee_cents, seller_net_cents, status, metadata_json, created_at, updated_at)
                VALUES (?, ?, 'merchant', 'marketplace_product', ?, ?, ?, ?, ?, 'created', ?, ?, ?)
                """,
                (buyer_id, seller_user_id, l["listing_id"], line_amount, currency,
                 line_fee, line_amount - line_fee,
                 json.dumps({"title": l["title"], "qty": l["qty"], "cart_line_id": l["line_id"]}, default=str),
                 now, now),
            )
            tx_ids.append(int(cur.lastrowid))

        if not bot.STRIPE_SECRET_KEY:
            for tx_id in tx_ids:
                cur.execute("UPDATE seller_transactions SET status='blocked_stripe_not_configured', updated_at=? WHERE id=?", (now, tx_id))
            return _error("Stripe checkout is not configured yet. No card was charged.", 503, transaction_ids=tx_ids)
        if not payout.get("connected_account_id"):
            for tx_id in tx_ids:
                cur.execute("UPDATE seller_transactions SET status='blocked_payout_onboarding_required', updated_at=? WHERE id=?", (now, tx_id))
            return _error("Seller payout onboarding is required before checkout.", 409, transaction_ids=tx_ids)

        try:
            base = (bot.APP_BASE_URL or request.url_root.rstrip("/")).rstrip("/")
            primary_tx = tx_ids[0]
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
                payment_intent_data={"application_fee_amount": platform_fee,
                                      "transfer_data": {"destination": payout.get("connected_account_id")}},
                metadata={"seller_transaction_ids": ",".join(str(t) for t in tx_ids),
                          "cart_checkout": "1",
                          "buyer_user_id": str(buyer_id),
                          "seller_user_id": str(seller_user_id)},
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
            for tx_id in tx_ids:
                cur.execute(
                    "UPDATE seller_transactions SET status='checkout_failed', metadata_json=?, updated_at=? WHERE id=?",
                    (json.dumps({"error": str(exc), "trace_id": trace_id}, default=str), now, tx_id),
                )
            return _error("Checkout could not be created.", 500, trace_id=trace_id, transaction_ids=tx_ids)

    return _with_db(handler)


def register(app) -> None:
    app.register_blueprint(cart_blueprint)
