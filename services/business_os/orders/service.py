"""Business OS — Section 5: Orders service (canonical domain facade).

Flag-gated (``BUSINESS_OS_ORDERS``) thin surface over the single canonical order
engine that physically lives in the marketplace vertical. Every public function here
delegates to ``marketplace.orders`` / ``marketplace.refunds`` — this module owns NO
table and NO ledger account of its own. The vocabularies (statuses, transitions) are
re-exported from the engine so there is exactly one definition.

Contract:

  * DARK when ``BUSINESS_OS_ORDERS`` is off — every entry point raises
    ``OrderError(..., 503, "disabled")``;
  * the underlying engine is itself gated behind ``BUSINESS_OS_MARKETPLACE``; a write
    attempted while marketplace is off surfaces the engine's own 503 — this module
    never bypasses that gate;
  * identity (buyer/seller) is passed through from the authenticated caller, never
    the request body;
  * reads are ownership-scoped (buyer or seller only) exactly as the engine enforces.

``OrderError`` is an alias of the engine's error type so callers get one stable name
without a second exception hierarchy.
"""

from __future__ import annotations

import os
from typing import Any, Optional

from services.business_os.marketplace import orders as _orders
from services.business_os.marketplace import refunds as _refunds
from services.business_os.marketplace.service import MarketplaceError


FLAG_ENV = "BUSINESS_OS_ORDERS"

# One stable domain-facing error name; NOT a second hierarchy.
OrderError = MarketplaceError

# Single source of truth for the vocabulary — re-exported, never redefined.
ORDER_STATUSES = _orders.ORDER_STATUSES
ALLOWED_ORDER_TRANSITIONS = _orders.ALLOWED_ORDER_TRANSITIONS


def is_enabled() -> bool:
    raw = (os.environ.get(FLAG_ENV) or "").strip().lower()
    return raw in {"1", "true", "on", "yes", "enabled", "canonical"}


def _require_enabled() -> None:
    if not is_enabled():
        raise OrderError("Orders is not enabled in this environment.", 503, "disabled")


# ============================================================================
# Order lifecycle (delegates to the canonical marketplace order state machine)
# ============================================================================
def create_order(buyer_user_id: Any, product_id: Any, *, quantity: int = 1,
                 context: Optional[dict] = None) -> dict:
    _require_enabled()
    return _orders.create_order(buyer_user_id, product_id, quantity=quantity,
                                context=context)


def get_order(order_id: Any, *, requester_user_id: Any = None) -> Optional[dict]:
    _require_enabled()
    return _orders.get_order(order_id, requester_user_id=requester_user_id)


def get_order_detail(order_id: Any, *, requester_user_id: Any = None) -> Optional[dict]:
    """Order header + line items + event history, ownership-scoped."""
    _require_enabled()
    order = _orders.get_order(order_id, requester_user_id=requester_user_id)
    if order is None:
        return None
    order["items"] = _orders.get_order_items(order_id)
    order["events"] = _orders.get_order_events(order_id)
    return order


def list_orders(*, buyer_user_id: Any = None, seller_user_id: Any = None,
                status: Optional[str] = None, limit: int = 200) -> list:
    _require_enabled()
    return _orders.list_orders(buyer_user_id=buyer_user_id,
                               seller_user_id=seller_user_id, status=status, limit=limit)


def pay_order(order_id: Any, buyer_user_id: Any, *,
              context: Optional[dict] = None) -> dict:
    _require_enabled()
    return _orders.pay_order(order_id, buyer_user_id, context=context)


def fulfill_order(order_id: Any, seller_user_id: Any, *,
                  tracking_ref: Optional[str] = None,
                  context: Optional[dict] = None) -> dict:
    _require_enabled()
    return _orders.fulfill_order(order_id, seller_user_id, tracking_ref=tracking_ref,
                                 context=context)


def complete_order(order_id: Any, buyer_user_id: Any, *,
                   context: Optional[dict] = None) -> dict:
    _require_enabled()
    return _orders.complete_order(order_id, buyer_user_id, context=context)


def cancel_order(order_id: Any, buyer_user_id: Any, *, reason: Optional[str] = None,
                 context: Optional[dict] = None) -> dict:
    _require_enabled()
    return _orders.cancel_order(order_id, buyer_user_id, reason=reason, context=context)


def order_money_summary(order_id: Any) -> dict:
    _require_enabled()
    return _orders.order_money_summary(order_id)


# ============================================================================
# Refunds / disputes / payout accrual (canonical refunds engine)
# ============================================================================
def refund_order(order_id: Any, *, amount_cents: Optional[int] = None,
                 reason: str, actor: Any = None) -> dict:
    _require_enabled()
    return _refunds.refund_order(order_id, amount_cents=amount_cents, reason=reason,
                                 actor=actor)


def list_refunds(order_id: Any) -> list:
    _require_enabled()
    return _refunds.list_refunds(order_id)


def open_dispute(order_id: Any, buyer_user_id: Any, *, reason: str) -> dict:
    _require_enabled()
    return _refunds.open_dispute(order_id, buyer_user_id, reason=reason)


def get_dispute(dispute_id: Any) -> Optional[dict]:
    _require_enabled()
    return _refunds.get_dispute(dispute_id)


def resolve_dispute(dispute_id: Any, *, resolution: str, actor: Any, reason: str) -> dict:
    _require_enabled()
    return _refunds.resolve_dispute(dispute_id, resolution=resolution, actor=actor,
                                    reason=reason)


def list_disputes(*, status: Optional[str] = None, order_id: Any = None) -> list:
    _require_enabled()
    return _refunds.list_disputes(status=status, order_id=order_id)


def seller_payout_balance(seller_user_id: Any, currency: str = "usd") -> dict:
    _require_enabled()
    return _refunds.seller_payout_balance(seller_user_id, currency=currency)
