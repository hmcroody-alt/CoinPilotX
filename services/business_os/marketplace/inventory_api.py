"""Business OS — Marketplace INVENTORY: framework-agnostic HTTP controller.

Same contract as the sibling controllers (``offers_api.py``, ``returns_api.py``):
bot.py passes an already-authenticated identity + parsed input; every handler
returns ``(int status_code, dict body)`` with an ``ok`` bool; the surface is
DARK (404) when ``BUSINESS_OS_MARKETPLACE`` is off; only curated
``MarketplaceError`` messages surface.

Intended mount (when bot.py is quiet enough to touch):

    GET  /api/business-os/marketplace/inventory                       -> get_overview
    GET  /api/business-os/marketplace/inventory/adjustments           -> list_adjustments
    POST /api/business-os/marketplace/products/<id>/inventory/adjust  -> adjust

Route-pack init must call ``inventory.ensure_schema()`` once.
"""

from __future__ import annotations

from typing import Any, Optional

from services.business_os.marketplace import inventory as invm
from services.business_os.marketplace import service as mkt
from services.business_os.marketplace.service import MarketplaceError


ADJUST_FIELDS = {"delta", "set_qty", "reason", "note"}


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
def get_overview(seller_user_id: Any, *,
                 low_stock_threshold: Optional[int] = None):
    if not mkt.is_enabled():
        return _dark()
    try:
        kwargs = {}
        if low_stock_threshold is not None:
            kwargs["low_stock_threshold"] = low_stock_threshold
        return (200, {"ok": True,
                      "overview": invm.inventory_overview(seller_user_id, **kwargs)})
    except MarketplaceError as exc:
        return _err(exc)


def list_adjustments(seller_user_id: Any, *, product_id: Any = None,
                     limit: int = 200):
    if not mkt.is_enabled():
        return _dark()
    try:
        rows = invm.list_adjustments(seller_user_id, product_id=product_id,
                                     limit=limit)
    except MarketplaceError as exc:
        return _err(exc)
    return (200, {"ok": True, "adjustments": rows})


def adjust(seller_user_id: Any, product_id: str, payload: Any = None, *,
           context: Optional[dict] = None):
    if not mkt.is_enabled():
        return _dark()
    try:
        f = _allowlist(payload, ADJUST_FIELDS)
        record = invm.adjust_inventory(
            seller_user_id, product_id, delta=f.get("delta"),
            set_qty=f.get("set_qty"), reason=f.get("reason"),
            note=f.get("note"), context=context)
    except MarketplaceError as exc:
        return _err(exc)
    return (201, {"ok": True, "adjustment": record})
