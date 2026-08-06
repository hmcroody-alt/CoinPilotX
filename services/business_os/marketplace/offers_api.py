"""Business OS — Marketplace OFFERS: framework-agnostic HTTP controller.

Same contract as ``marketplace/api.py`` (see its module docstring): bot.py owns
auth/CSRF/RBAC and passes an already-authenticated identity + parsed input;
every handler returns ``(int status_code, dict body)`` with an ``ok`` bool; the
whole surface is DARK (404) when ``BUSINESS_OS_MARKETPLACE`` is off; only
curated ``MarketplaceError`` messages are surfaced; lifecycle is expressed as
server-mapped VERBS — a client can never write a status.

Kept in its own module rather than appended to ``api.py`` so the addition is
purely additive while other agents work the repo; registration in bot.py is a
thin adapter over these functions plus one ``offers.ensure_schema()`` call at
route-pack init (the same shape every other pack uses).

Intended mount (when bot.py is quiet enough to touch):

    POST   /api/business-os/marketplace/offers                  -> create_offer
    GET    /api/business-os/marketplace/offers?role=&status=    -> list_own_offers
    GET    /api/business-os/marketplace/offers/<id>             -> get_offer
    POST   /api/business-os/marketplace/offers/<id>/<action>    -> act_on_offer
    POST   /admin/business-os/marketplace/offers/expire         -> run_expiry_sweep
"""

from __future__ import annotations

from typing import Any, Optional

from services.business_os.marketplace import offers as off
from services.business_os.marketplace import service as mkt
from services.business_os.marketplace.service import MarketplaceError


OFFER_CREATE_FIELDS = {"product_id", "amount_cents", "quantity", "expires_in_hours"}
OFFER_COUNTER_FIELDS = {"amount_cents", "expires_in_hours"}
OFFER_DECLINE_FIELDS = {"reason"}
OFFER_ACCEPT_FIELDS = set()
OFFER_EMPTY_FIELDS = set()

# The verbs a client may issue. Values: (service fn name, allowed payload fields).
OFFER_ACTIONS = {
    "accept": OFFER_ACCEPT_FIELDS,
    "decline": OFFER_DECLINE_FIELDS,
    "counter": OFFER_COUNTER_FIELDS,
    "withdraw": OFFER_EMPTY_FIELDS,
    "convert": OFFER_EMPTY_FIELDS,
}

LIST_ROLES = {"buyer", "seller"}


def _dark():
    return (404, {"ok": False, "error": "Not found."})


def _err(exc: MarketplaceError):
    return (exc.http_status, {"ok": False, "error": str(exc), "code": exc.code})


def _allowlist(payload: Any, allowed: set) -> dict:
    if payload is None:
        return {}
    if not isinstance(payload, dict):
        raise MarketplaceError("Invalid request body.", 400, "bad_body")
    unknown = set(payload) - allowed
    if unknown:
        raise MarketplaceError(f"Unknown field(s): {sorted(unknown)}.", 400, "unknown_field")
    return {k: payload[k] for k in payload}


# --- handlers ----------------------------------------------------------------
def create_offer(buyer_user_id: Any, payload: Any = None, *,
                 context: Optional[dict] = None):
    if not mkt.is_enabled():
        return _dark()
    try:
        f = _allowlist(payload, OFFER_CREATE_FIELDS)
        if not f.get("product_id"):
            raise MarketplaceError("product_id is required.", 400, "invalid")
        offer = off.create_offer(
            buyer_user_id, f["product_id"], f.get("amount_cents"),
            quantity=f.get("quantity", 1),
            expires_in_hours=f.get("expires_in_hours"), context=context)
    except MarketplaceError as exc:
        return _err(exc)
    return (201, {"ok": True, "offer": offer})


def get_offer(user_id: Any, offer_id: str):
    if not mkt.is_enabled():
        return _dark()
    offer = off.get_offer(offer_id, requester_user_id=user_id)
    if offer is None:
        return (404, {"ok": False, "error": "Not found.", "code": "not_found"})
    return (200, {"ok": True, "offer": offer,
                  "events": off.get_offer_events(offer_id)})


def list_own_offers(user_id: Any, *, role: str = "buyer",
                    status: Optional[str] = None):
    if not mkt.is_enabled():
        return _dark()
    if role not in LIST_ROLES:
        return _err(MarketplaceError(f"role must be one of {sorted(LIST_ROLES)}.",
                                     400, "bad_role"))
    try:
        if role == "buyer":
            offers = off.list_offers(buyer_user_id=user_id, status=status)
        else:
            offers = off.list_offers(seller_user_id=user_id, status=status)
    except MarketplaceError as exc:
        return _err(exc)
    return (200, {"ok": True, "offers": offers})


def act_on_offer(user_id: Any, offer_id: str, action: str, payload: Any = None, *,
                 context: Optional[dict] = None):
    """One dispatch point for every lifecycle verb, so the route table stays a
    single line per verb and an unknown verb is a 400 — never a silent no-op."""
    if not mkt.is_enabled():
        return _dark()
    if action not in OFFER_ACTIONS:
        return _err(MarketplaceError(
            f"Unknown action {action!r}. Allowed: {sorted(OFFER_ACTIONS)}.",
            400, "bad_action"))
    try:
        f = _allowlist(payload, OFFER_ACTIONS[action])
        if action == "accept":
            offer = off.accept_offer(offer_id, user_id, context=context)
        elif action == "decline":
            offer = off.decline_offer(offer_id, user_id, reason=f.get("reason"),
                                      context=context)
        elif action == "counter":
            offer = off.counter_offer(offer_id, user_id, f.get("amount_cents"),
                                      expires_in_hours=f.get("expires_in_hours"),
                                      context=context)
        elif action == "withdraw":
            offer = off.withdraw_offer(offer_id, user_id, context=context)
        else:  # convert
            offer = off.convert_offer(offer_id, user_id, context=context)
    except MarketplaceError as exc:
        return _err(exc)
    body = {"ok": True, "offer": offer}
    if action == "convert":
        body["order_id"] = offer.get("converted_order_id")
    return (200, body)


def run_expiry_sweep(actor: Any = None):
    """Admin/worker entry point. bot.py gates WHO may call this; the sweep itself
    is idempotent and safe to run on a schedule."""
    if not mkt.is_enabled():
        return _dark()
    try:
        n = off.expire_offers()
    except MarketplaceError as exc:
        return _err(exc)
    return (200, {"ok": True, "expired": n, "actor": None if actor is None else str(actor)})
