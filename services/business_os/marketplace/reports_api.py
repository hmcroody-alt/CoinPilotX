"""Business OS — Marketplace SELLER REPORTS: framework-agnostic HTTP controller.

Same contract as the sibling controllers; both endpoints are READ-ONLY.
DARK (404) when ``BUSINESS_OS_MARKETPLACE`` is off; curated errors only.

Intended mount (when bot.py is quiet enough to touch):

    GET /api/business-os/marketplace/seller/reports/finance    -> get_finance
    GET /api/business-os/marketplace/seller/reports/sales-by-day -> get_sales_by_day

No schema of its own.
"""

from __future__ import annotations

from typing import Any, Optional

from services.business_os.marketplace import reports as rpt
from services.business_os.marketplace import service as mkt
from services.business_os.marketplace.service import MarketplaceError


def _dark():
    return (404, {"ok": False, "error": "Not found.", "code": "not_found"})


def _err(exc: MarketplaceError):
    return (exc.http_status, {"ok": False, "error": str(exc), "code": exc.code})


def get_finance(seller_user_id: Any, *, currency: str = "usd"):
    if not mkt.is_enabled():
        return _dark()
    try:
        return (200, {"ok": True,
                      "report": rpt.finance_report(seller_user_id,
                                                   currency=currency)})
    except MarketplaceError as exc:
        return _err(exc)


def get_sales_by_day(seller_user_id: Any, *, currency: str = "usd",
                     start_day: Optional[str] = None,
                     end_day: Optional[str] = None):
    if not mkt.is_enabled():
        return _dark()
    try:
        return (200, {"ok": True,
                      "report": rpt.sales_by_day(seller_user_id,
                                                 currency=currency,
                                                 start_day=start_day,
                                                 end_day=end_day)})
    except MarketplaceError as exc:
        return _err(exc)
