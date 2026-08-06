"""Business OS — Marketplace RETURNS: framework-agnostic HTTP controller.

Same contract as ``marketplace/api.py`` / ``offers_api.py``: bot.py owns
auth/CSRF/RBAC and passes an already-authenticated identity + parsed input;
every handler returns ``(int status_code, dict body)`` with an ``ok`` bool; the
whole surface is DARK (404) when ``BUSINESS_OS_MARKETPLACE`` is off; only
curated ``MarketplaceError`` messages surface; lifecycle is server-mapped VERBS.

Kept in its own module so the addition is purely additive while other agents
work the repo. Registration in bot.py is a thin adapter over these functions
plus one ``returns.ensure_schema()`` call at route-pack init.

Intended mount (when bot.py is quiet enough to touch):

    POST   /api/business-os/marketplace/returns                 -> create_return
    GET    /api/business-os/marketplace/returns?role=&status=   -> list_own_returns
    GET    /api/business-os/marketplace/returns/<id>            -> get_return
    POST   /api/business-os/marketplace/returns/<id>/<action>   -> act_on_return
"""

from __future__ import annotations

from typing import Any, Optional

from services.business_os.marketplace import returns as retm
from services.business_os.marketplace import service as mkt
from services.business_os.marketplace.service import MarketplaceError


RETURN_CREATE_FIELDS = {"order_id", "reason", "product_id", "quantity"}

# verb -> (allowed payload fields). The engine decides WHO may act (buyer vs
# seller); the wrong party gets the same 404 the engine gives strangers.
RETURN_ACTIONS = {
    "approve": set(),
    "decline": {"reason"},
    "cancel": set(),
    "receive": set(),
    "refund": {"amount_cents"},
    "close": {"reason"},
}

LIST_ROLES = {"buyer", "seller"}


def _dark():
    return (404, {"ok": False, "error": "Not found.", "code": "not_found"})


def _err(exc: MarketplaceError):
    return (exc.http_status, {"ok": False, "error": str(exc), "code": exc.code})


def _allowlist(payload: Any, allowed: set) -> dict:
    if payload is None:
        return {}
    if not isinstance(payload, dict):
        raise MarketplaceError("Invalid request body.", 400, "bad_body")
    unknown = set(payload) - allowed
    if unknown:
        raise MarketplaceError(f"Unknown field(s): {sorted(unknown)}.",
                               400, "unknown_field")
    return {k: payload[k] for k in payload}


# --- handlers ----------------------------------------------------------------
def create_return(buyer_user_id: Any, payload: Any = None, *,
                  context: Optional[dict] = None):
    if not mkt.is_enabled():
        return _dark()
    try:
        f = _allowlist(payload, RETURN_CREATE_FIELDS)
        if not f.get("order_id"):
            raise MarketplaceError("order_id is required.", 400, "invalid")
        ret = retm.request_return(
            buyer_user_id, f["order_id"], reason=f.get("reason"),
            product_id=f.get("product_id"), quantity=f.get("quantity"),
            context=context)
    except MarketplaceError as exc:
        return _err(exc)
    return (201, {"ok": True, "return": ret})


def get_return(user_id: Any, return_id: str):
    if not mkt.is_enabled():
        return _dark()
    ret = retm.get_return(return_id, requester_user_id=user_id)
    if ret is None:
        return (404, {"ok": False, "error": "Not found.", "code": "not_found"})
    return (200, {"ok": True, "return": ret,
                  "events": retm.get_return_events(return_id)})


def list_own_returns(user_id: Any, *, role: str = "buyer",
                     status: Optional[str] = None):
    if not mkt.is_enabled():
        return _dark()
    if role not in LIST_ROLES:
        return _err(MarketplaceError(f"role must be one of {sorted(LIST_ROLES)}.",
                                     400, "bad_role"))
    try:
        if role == "buyer":
            rows = retm.list_returns(buyer_user_id=user_id, status=status)
        else:
            rows = retm.list_returns(seller_user_id=user_id, status=status)
    except MarketplaceError as exc:
        return _err(exc)
    return (200, {"ok": True, "returns": rows})


def act_on_return(user_id: Any, return_id: str, action: str, payload: Any = None, *,
                  context: Optional[dict] = None):
    """One dispatch point for every lifecycle verb; unknown verbs are a 400,
    never a silent no-op."""
    if not mkt.is_enabled():
        return _dark()
    if action not in RETURN_ACTIONS:
        return _err(MarketplaceError(
            f"Unknown action {action!r}. Allowed: {sorted(RETURN_ACTIONS)}.",
            400, "bad_action"))
    try:
        f = _allowlist(payload, RETURN_ACTIONS[action])
        if action == "approve":
            ret = retm.approve_return(return_id, user_id, context=context)
        elif action == "decline":
            ret = retm.decline_return(return_id, user_id,
                                      reason=f.get("reason"), context=context)
        elif action == "cancel":
            ret = retm.cancel_return(return_id, user_id, context=context)
        elif action == "receive":
            ret = retm.mark_received(return_id, user_id, context=context)
        elif action == "refund":
            ret = retm.refund_return(return_id, user_id,
                                     amount_cents=f.get("amount_cents"),
                                     context=context)
        else:  # close
            ret = retm.close_return(return_id, user_id,
                                    reason=f.get("reason"), context=context)
    except MarketplaceError as exc:
        return _err(exc)
    body = {"ok": True, "return": ret}
    if action == "refund":
        body["refund"] = ret.get("refund")
    return (200, body)
