"""Business OS — Marketplace: framework-agnostic HTTP controller (Stage 3 Part 7).

bot.py owns authentication, CSRF, and RBAC; it then calls these pure functions with an
*already-authenticated* identity and parsed input, and turns the returned
``(status_code, body)`` tuple into a Flask JSON response. Keeping the decision logic here
(not inline in bot.py) makes every branch unit-testable without importing
Flask/stripe/telegram (bot.py is not importable in the hermetic sandbox).

Contract for every handler (mirrors the advertising controller exactly):

  * returns ``(int status_code, dict body)``; ``body`` always has an ``ok`` bool;
  * the whole canonical surface is DARK when ``BUSINESS_OS_MARKETPLACE`` is off — every
    handler returns 404 so no partial canonical path is exposed;
  * ownership is enforced in the service (non-owner ⇒ 404, existence not leaked);
  * only the curated ``MarketplaceError`` message is surfaced — never an internal
    exception string;
  * clients may never set a status/id/fee/owner directly; lifecycle is expressed as
    server-mapped verbs, and all money math is server-authoritative.

Identity (``owner_user_id`` / ``buyer_user_id`` / ``seller_user_id``) is passed in by
bot.py, derived from the session/token — never from the request body. ``context`` carries
fresh ``{account_status, access_enabled}`` so account-hold precedence is evaluated live.
Admin handlers additionally receive the RBAC ``actor`` (the acting admin) from bot.py.
"""

from __future__ import annotations

from typing import Any, Optional

from services.business_os import results as _results
from services.business_os.marketplace import service as mkt
from services.business_os.marketplace import orders as mko
from services.business_os.marketplace import refunds as mkr
from services.business_os.marketplace import money as mkmoney
from services.business_os.marketplace import assistant as mka
from services.business_os.marketplace import admin as mkadmin
from services.business_os.marketplace.service import MarketplaceError


# --- client-supplied field allowlists (anything else is rejected) -----------
SELLER_REGISTER_FIELDS = {"display_name", "notes"}
PRODUCT_CREATE_FIELDS = {"title", "price_cents", "description", "currency",
                         "fulfillment_type", "inventory_qty"}
PRODUCT_UPDATE_FIELDS = {"title", "description", "price_cents"}
INVENTORY_FIELDS = {"inventory_qty"}
ORDER_CREATE_FIELDS = {"product_id", "quantity"}
FULFILL_FIELDS = {"tracking_ref"}
CANCEL_FIELDS = {"reason"}
DISPUTE_FIELDS = {"reason"}
REVIEW_FIELDS = {"product_id", "order_id", "rating", "body"}
ASSISTANT_FIELDS = {"tool", "params", "confirmation_token"}
# Governed admin fields — a reason is mandatory server-side.
ADMIN_REFUND_FIELDS = {"amount_cents", "reason", "idempotency_key"}
ADMIN_DISPUTE_FIELDS = {"decision", "reason", "refund_amount_cents"}
ADMIN_REASON_FIELDS = {"reason"}
ADMIN_PAYOUT_NOTE_FIELDS = {"amount_cents", "provider_reference", "reason", "currency"}
APPEAL_FIELDS = {"reason", "order_id"}
APPEAL_RESOLVE_FIELDS = {"decision", "reason"}

# Product lifecycle verbs a client may issue (mapped to service actions 1:1).
PRODUCT_ACTIONS = {"publish", "pause", "resume", "archive", "restore"}


def _dark():
    return (404, {"ok": False, "error": "Not found."})


def _enabled() -> bool:
    return mkt.is_enabled()


def _err(exc: MarketplaceError):
    return (exc.http_status, {"ok": False, "error": str(exc), "code": exc.code})


def _allowlist(payload: Any, allowed: set) -> dict:
    """Return only allowlisted keys; reject unknown keys (400 unknown_field) so silent
    field-dropping never hides a client bug or injection."""
    if payload is None:
        return {}
    if not isinstance(payload, dict):
        raise MarketplaceError("Invalid request body.", 400, "bad_body")
    unknown = set(payload) - allowed
    if unknown:
        raise MarketplaceError(f"Unknown field(s): {sorted(unknown)}.", 400, "unknown_field")
    return {k: payload[k] for k in payload}


# --- seller registration + catalog ------------------------------------------
def register_seller(owner_user_id: Any, payload: Any = None):
    if not _enabled():
        return _dark()
    try:
        fields = _allowlist(payload, SELLER_REGISTER_FIELDS)
        seller = mkt.upsert_seller(owner_user_id, display_name=fields.get("display_name"),
                                   notes=fields.get("notes"))
    except MarketplaceError as exc:
        return _err(exc)
    return (200, {"ok": True, "seller": seller})


def get_own_seller(owner_user_id: Any):
    if not _enabled():
        return _dark()
    seller = mkt.get_seller(owner_user_id)
    if seller is None:
        return (404, {"ok": False, "error": "Not found.", "code": "not_found"})
    return (200, {"ok": True, "seller": seller})


def create_product(owner_user_id: Any, payload: Any = None, *,
                   context: Optional[dict] = None):
    if not _enabled():
        return _dark()
    try:
        f = _allowlist(payload, PRODUCT_CREATE_FIELDS)
        product = mkt.create_product(
            owner_user_id, title=f.get("title"), price_cents=f.get("price_cents"),
            description=f.get("description"), currency=f.get("currency", "usd"),
            fulfillment_type=f.get("fulfillment_type", "physical"),
            inventory_qty=f.get("inventory_qty"), context=context)
    except MarketplaceError as exc:
        return _err(exc)
    return (201, {"ok": True, "product": product})


def list_own_products(owner_user_id: Any, *, status: Optional[str] = None):
    if not _enabled():
        return _dark()
    if status is not None and status not in mkt.PRODUCT_STATUSES:
        return _err(MarketplaceError(f"Unknown status filter: {status!r}.", 400, "bad_status"))
    return (200, {"ok": True, "products": mkt.list_products(owner_user_id, status=status)})


def get_own_product(owner_user_id: Any, product_id: str):
    if not _enabled():
        return _dark()
    try:
        product = mkt.get_product(product_id, requester_user_id=owner_user_id)
    except MarketplaceError as exc:
        return _err(exc)
    if product is None:
        return (404, {"ok": False, "error": "Not found.", "code": "not_found"})
    return (200, {"ok": True, "product": product})


def update_product(owner_user_id: Any, product_id: str, payload: Any = None, *,
                   context: Optional[dict] = None):
    if not _enabled():
        return _dark()
    try:
        fields = _allowlist(payload, PRODUCT_UPDATE_FIELDS)
        if not fields:
            raise MarketplaceError("No fields to update.", 400, "no_fields")
        product = mkt.update_product(owner_user_id, product_id, fields=fields,
                                     context=context)
    except MarketplaceError as exc:
        return _err(exc)
    return (200, {"ok": True, "product": product})


def set_inventory(owner_user_id: Any, product_id: str, payload: Any = None, *,
                  context: Optional[dict] = None):
    if not _enabled():
        return _dark()
    try:
        f = _allowlist(payload, INVENTORY_FIELDS)
        if "inventory_qty" not in f:
            raise MarketplaceError("inventory_qty is required.", 400, "no_fields")
        product = mkt.set_inventory(owner_user_id, product_id, f.get("inventory_qty"),
                                    context=context)
    except MarketplaceError as exc:
        return _err(exc)
    return (200, {"ok": True, "product": product})


def product_lifecycle(owner_user_id: Any, product_id: str, action: str, *,
                      context: Optional[dict] = None):
    """Product lifecycle verb: publish/pause/resume/archive/restore. No raw status in."""
    if not _enabled():
        return _dark()
    act = (action or "").strip().lower()
    if act not in PRODUCT_ACTIONS:
        return _err(MarketplaceError(f"Unknown product action: {action!r}.", 400, "bad_action"))
    try:
        product = mkt.transition_product(owner_user_id, product_id, act, context=context)
    except MarketplaceError as exc:
        return _err(exc)
    return (200, {"ok": True, "product": product})


# --- public storefront (read-only, only 'active' products) ------------------
def public_list_products(*, seller_user_id: Optional[Any] = None, limit: int = 100):
    if not _enabled():
        return _dark()
    rows = mkt.list_active_products(seller_user_id=seller_user_id, limit=limit)
    return (200, {"ok": True, "products": [mkt.public_product(r) for r in rows]})


def public_get_product(product_id: str):
    if not _enabled():
        return _dark()
    product = mkt.get_product(product_id, for_public=True)
    if product is None:
        return (404, {"ok": False, "error": "Not found.", "code": "not_found"})
    return (200, {"ok": True, "product": mkt.public_product(product)})


def product_reviews(product_id: str):
    if not _enabled():
        return _dark()
    return (200, {"ok": True, "summary": mkr.product_rating_summary(product_id),
                  "reviews": mkr.list_reviews(product_id)})


# --- buyer order flow -------------------------------------------------------
def create_order(buyer_user_id: Any, payload: Any = None, *,
                 context: Optional[dict] = None):
    if not _enabled():
        return _dark()
    try:
        f = _allowlist(payload, ORDER_CREATE_FIELDS)
        if f.get("product_id") is None:
            raise MarketplaceError("product_id is required.", 400, "product_id_required")
        order = mko.create_order(buyer_user_id, f.get("product_id"),
                                 quantity=int(f.get("quantity") or 1), context=context)
    except MarketplaceError as exc:
        return _err(exc)
    return (201, {"ok": True, "order": order})


def get_order(requester_user_id: Any, order_id: str):
    """Buyer or seller may read their own order (with money summary). Others ⇒ 404."""
    if not _enabled():
        return _dark()
    order = mko.get_order(order_id, requester_user_id=requester_user_id)
    if order is None:
        return (404, {"ok": False, "error": "Not found.", "code": "not_found"})
    return (200, {"ok": True, "order": order,
                  "items": mko.get_order_items(order_id),
                  "money": mko.order_money_summary(order_id)})


def list_my_orders(user_id: Any, *, role: str = "buyer", status: Optional[str] = None):
    if not _enabled():
        return _dark()
    if role == "seller":
        rows = mko.list_orders(seller_user_id=user_id, status=status)
    else:
        rows = mko.list_orders(buyer_user_id=user_id, status=status)
    return (200, {"ok": True, "orders": rows})


def pay_order(buyer_user_id: Any, order_id: str, *, context: Optional[dict] = None):
    if not _enabled():
        return _dark()
    try:
        order = mko.pay_order(order_id, buyer_user_id, context=context)
    except MarketplaceError as exc:
        return _err(exc)
    return (200, {"ok": True, "order": order})


def complete_order(buyer_user_id: Any, order_id: str, *,
                   context: Optional[dict] = None):
    if not _enabled():
        return _dark()
    try:
        order = mko.complete_order(order_id, buyer_user_id, context=context)
    except MarketplaceError as exc:
        return _err(exc)
    return (200, {"ok": True, "order": order})


def cancel_order(buyer_user_id: Any, order_id: str, payload: Any = None, *,
                 context: Optional[dict] = None):
    if not _enabled():
        return _dark()
    try:
        f = _allowlist(payload, CANCEL_FIELDS)
        order = mko.cancel_order(order_id, buyer_user_id, reason=f.get("reason"),
                                 context=context)
    except MarketplaceError as exc:
        return _err(exc)
    return (200, {"ok": True, "order": order})


def open_dispute(buyer_user_id: Any, order_id: str, payload: Any = None, *,
                 context: Optional[dict] = None):
    if not _enabled():
        return _dark()
    try:
        f = _allowlist(payload, DISPUTE_FIELDS)
        dispute = mkr.open_dispute(order_id, buyer_user_id, reason=f.get("reason"),
                                   context=context)
    except MarketplaceError as exc:
        return _err(exc)
    return (201, {"ok": True, "dispute": dispute})


def create_review(buyer_user_id: Any, payload: Any = None, *,
                  context: Optional[dict] = None):
    if not _enabled():
        return _dark()
    try:
        f = _allowlist(payload, REVIEW_FIELDS)
        review = mkr.create_review(
            buyer_user_id, product_id=f.get("product_id"), order_id=f.get("order_id"),
            rating=f.get("rating"), body=f.get("body"), context=context)
    except MarketplaceError as exc:
        return _err(exc)
    return (201, {"ok": True, "review": review})


# --- seller order flow ------------------------------------------------------
def fulfill_order(seller_user_id: Any, order_id: str, payload: Any = None, *,
                  context: Optional[dict] = None):
    if not _enabled():
        return _dark()
    try:
        f = _allowlist(payload, FULFILL_FIELDS)
        order = mko.fulfill_order(order_id, seller_user_id,
                                  tracking_ref=f.get("tracking_ref"), context=context)
    except MarketplaceError as exc:
        return _err(exc)
    return (200, {"ok": True, "order": order})


def seller_payout_balance(seller_user_id: Any, *, currency: str = "usd"):
    if not _enabled():
        return _dark()
    return (200, {"ok": True, "payout": mkr.seller_payout_balance(seller_user_id, currency)})


# --- seller money read surface (balances, activity, disputes) ---------------
# Read-only by construction: every one of these delegates to money.py, which
# holds no write primitive. A payments client needs all three and should not be
# assembling any of them itself.
def seller_money_overview(seller_user_id: Any, *, currency: str = "usd"):
    if not _enabled():
        return _dark()
    try:
        return (200, {"ok": True,
                      "money": mkmoney.seller_money_overview(seller_user_id, currency)})
    except MarketplaceError as exc:
        return _err(exc)


def seller_activity(seller_user_id: Any, *, currency: str = "usd",
                    limit: int = 25, cursor: Optional[str] = None,
                    entry_types: Optional[Any] = None):
    if not _enabled():
        return _dark()
    from services.business_os.ledger.ledger import LedgerError
    try:
        return (200, {"ok": True,
                      "activity": mkmoney.seller_activity(
                          seller_user_id, currency, limit=limit,
                          before_cursor=cursor, entry_types=entry_types)})
    except MarketplaceError as exc:
        return _err(exc)
    except LedgerError as exc:
        # A cursor the client invented is a client error, not a 500. Any other
        # failure propagates: a ledger that is genuinely unhappy must not be
        # dressed up as a user mistake.
        return (400, {"ok": False, "error": str(exc), "code": "bad_cursor"})


def seller_disputes(seller_user_id: Any, *, status: Optional[str] = "open",
                    limit: int = 50):
    if not _enabled():
        return _dark()
    try:
        return (200, {"ok": True,
                      "disputes": mkmoney.seller_disputes(
                          seller_user_id, status=status, limit=limit)})
    except MarketplaceError as exc:
        return _err(exc)


# --- governed assistant -----------------------------------------------------
def assistant_plan(user_id: Any, payload: Any = None):
    if not _enabled():
        return _dark()
    try:
        f = _allowlist(payload, ASSISTANT_FIELDS)
        if not f.get("tool"):
            raise MarketplaceError("tool is required.", 400, "tool_required")
        plan = mka.plan(user_id, f.get("tool"), f.get("params") or {})
    except MarketplaceError as exc:
        return _err(exc)
    return (200, {"ok": True, "plan": plan})


def assistant_execute(user_id: Any, payload: Any = None):
    if not _enabled():
        return _dark()
    try:
        f = _allowlist(payload, ASSISTANT_FIELDS)
        if not f.get("tool"):
            raise MarketplaceError("tool is required.", 400, "tool_required")
        out = mka.execute(user_id, f.get("tool"), f.get("params") or {},
                          confirmation_token=f.get("confirmation_token"))
    except MarketplaceError as exc:
        return _err(exc)
    # 200 only when canonical state confirmed the action. An unverified write returns
    # 409 + ok:False, so neither the status code nor `ok` can be read as success.
    return _results.envelope(out)


def assistant_tools(user_id: Any):
    if not _enabled():
        return _dark()
    return (200, {"ok": True, "tools": mka.list_tools()})


# --- admin surface (RBAC actor supplied by bot.py) --------------------------
def admin_get_order(actor: Any, order_id: str):
    if not _enabled():
        return _dark()
    try:
        view = mkadmin.admin_get_order(order_id)
    except MarketplaceError as exc:
        return _err(exc)
    return (200, {"ok": True, **view})


def admin_list_orders(actor: Any, *, buyer_user_id: Optional[Any] = None,
                      seller_user_id: Optional[Any] = None,
                      status: Optional[str] = None):
    if not _enabled():
        return _dark()
    rows = mkadmin.admin_list_orders(buyer_user_id=buyer_user_id,
                                     seller_user_id=seller_user_id, status=status)
    return (200, {"ok": True, "orders": rows})


def admin_refund_order(actor: Any, order_id: str, payload: Any = None):
    if not _enabled():
        return _dark()
    try:
        f = _allowlist(payload, ADMIN_REFUND_FIELDS)
        out = mkadmin.admin_refund_order(order_id, actor=actor, reason=f.get("reason"),
                                         amount_cents=f.get("amount_cents"),
                                         idempotency_key=f.get("idempotency_key"))
    except MarketplaceError as exc:
        return _err(exc)
    return (200, {"ok": True, "refund": out})


def admin_list_disputes(actor: Any, *, status: Optional[str] = None,
                        order_id: Optional[Any] = None):
    if not _enabled():
        return _dark()
    return (200, {"ok": True,
                  "disputes": mkadmin.admin_list_disputes(status=status, order_id=order_id)})


def admin_resolve_dispute(actor: Any, dispute_id: str, payload: Any = None):
    if not _enabled():
        return _dark()
    try:
        f = _allowlist(payload, ADMIN_DISPUTE_FIELDS)
        if not f.get("decision"):
            raise MarketplaceError("decision is required.", 400, "decision_required")
        out = mkadmin.admin_resolve_dispute(
            dispute_id, f.get("decision"), actor=actor, reason=f.get("reason"),
            refund_amount_cents=f.get("refund_amount_cents"))
    except MarketplaceError as exc:
        return _err(exc)
    return (200, {"ok": True, "dispute": out})


def admin_restrict_seller(actor: Any, user_id: Any, payload: Any = None):
    if not _enabled():
        return _dark()
    try:
        f = _allowlist(payload, ADMIN_REASON_FIELDS)
        out = mkadmin.admin_restrict_seller(user_id, actor=actor, reason=f.get("reason"))
    except MarketplaceError as exc:
        return _err(exc)
    return (200, {"ok": True, "result": out})


def admin_lift_seller_restriction(actor: Any, user_id: Any, payload: Any = None):
    if not _enabled():
        return _dark()
    try:
        f = _allowlist(payload, ADMIN_REASON_FIELDS)
        out = mkadmin.admin_lift_seller_restriction(user_id, actor=actor,
                                                    reason=f.get("reason"))
    except MarketplaceError as exc:
        return _err(exc)
    return (200, {"ok": True, "result": out})


def submit_appeal(user_id: Any, payload: Any = None):
    if not _enabled():
        return _dark()
    try:
        f = _allowlist(payload, APPEAL_FIELDS)
        out = mkadmin.submit_appeal(user_id, reason=f.get("reason"),
                                    order_id=f.get("order_id"))
    except MarketplaceError as exc:
        return _err(exc)
    return (201, {"ok": True, "appeal": out})


def admin_list_appeals(actor: Any, *, user_id: Optional[Any] = None,
                       state: Optional[str] = None):
    if not _enabled():
        return _dark()
    return (200, {"ok": True,
                  "appeals": mkadmin.admin_list_appeals(user_id=user_id, state=state)})


def admin_resolve_appeal(actor: Any, appeal_id: Any, payload: Any = None):
    if not _enabled():
        return _dark()
    try:
        f = _allowlist(payload, APPEAL_RESOLVE_FIELDS)
        if not f.get("decision"):
            raise MarketplaceError("decision is required.", 400, "decision_required")
        out = mkadmin.admin_resolve_appeal(appeal_id, f.get("decision"), actor=actor,
                                           reason=f.get("reason"))
    except MarketplaceError as exc:
        return _err(exc)
    return (200, {"ok": True, "appeal": out})


def admin_seller_payout_balance(actor: Any, user_id: Any, *, currency: str = "usd"):
    if not _enabled():
        return _dark()
    return (200, {"ok": True,
                  "payout": mkadmin.admin_seller_payout_balance(user_id, currency)})


def admin_record_payout_note(actor: Any, user_id: Any, payload: Any = None):
    if not _enabled():
        return _dark()
    try:
        f = _allowlist(payload, ADMIN_PAYOUT_NOTE_FIELDS)
        out = mkadmin.admin_record_payout_note(
            user_id, actor=actor, reason=f.get("reason"),
            amount_cents=f.get("amount_cents"),
            provider_reference=f.get("provider_reference"),
            currency=f.get("currency", "usd"))
    except MarketplaceError as exc:
        return _err(exc)
    return (200, {"ok": True, "note": out})
