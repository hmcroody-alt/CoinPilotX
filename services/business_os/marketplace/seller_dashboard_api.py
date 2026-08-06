"""Business OS — Marketplace SELLER DASHBOARD: framework-agnostic HTTP controller.

Same contract as the sibling controllers (``offers_api.py``, ``returns_api.py``,
``inventory_api.py``): bot.py passes an already-authenticated identity + parsed
input; every handler returns ``(int status_code, dict body)`` with an ``ok``
bool; the surface is DARK (404) when ``BUSINESS_OS_MARKETPLACE`` is off; only
curated ``MarketplaceError`` messages surface.

Both endpoints are READ-ONLY projections — no payloads, no mutation, so there
is no field allowlist here. The zero-vs-unavailable semantics of the engine
pass through untouched: a queue count of ``None`` means UNAVAILABLE (subsystem
not initialised) and the client must render it as such, never as 0.

Intended mount (when bot.py is quiet enough to touch):

    GET /api/business-os/marketplace/seller/action-center  -> get_action_center
    GET /api/business-os/marketplace/seller/sales-summary  -> get_sales_summary

Route-pack init has no schema of its own; it reads sibling engines' tables.
"""

from __future__ import annotations

from typing import Any

from services.business_os.marketplace import seller_dashboard as dash
from services.business_os.marketplace import service as mkt
from services.business_os.marketplace.service import MarketplaceError


def _dark():
    return (404, {"ok": False, "error": "Not found.", "code": "not_found"})


def _err(exc: MarketplaceError):
    return (exc.http_status, {"ok": False, "error": str(exc), "code": exc.code})


# --- handlers ----------------------------------------------------------------
def get_action_center(seller_user_id: Any):
    if not mkt.is_enabled():
        return _dark()
    try:
        return (200, {"ok": True, "action_center": dash.action_center(seller_user_id)})
    except MarketplaceError as exc:
        return _err(exc)


def get_sales_summary(seller_user_id: Any, *, currency: str = "usd"):
    if not mkt.is_enabled():
        return _dark()
    try:
        return (200, {"ok": True,
                      "summary": dash.sales_summary(seller_user_id,
                                                    currency=currency)})
    except MarketplaceError as exc:
        return _err(exc)
