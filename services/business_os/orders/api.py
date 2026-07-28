"""Business OS — Section 5: Orders framework-agnostic HTTP controller.

Thin ``(status_code, body)`` controller over the canonical Orders service facade.
bot.py owns auth/CSRF/session and passes an already-authenticated ``actor_user_id``;
this module never trusts identity from the request body.

Contract (mirrors the Business HQ / Store / marketplace controllers):

  * returns ``(int status_code, dict body)``; ``body`` always carries an ``ok`` bool;
  * DARK when ``BUSINESS_OS_ORDERS`` is off — every handler returns 404 so no partial
    canonical path is exposed;
  * ownership + lifecycle rules are enforced by the engine (no role/ownership ⇒ 404,
    existence not leaked; illegal transition ⇒ 409);
  * only the curated ``OrderError`` message is surfaced — never a raw exception.

There is no second order system underneath — every handler resolves to the one
canonical marketplace order engine through :mod:`services.business_os.orders.service`.
"""

from __future__ import annotations

from typing import Any, Optional, Tuple

from services.business_os.orders import service as svc
from services.business_os.orders.service import OrderError


def _ok(body: dict, status: int = 200) -> Tuple[int, dict]:
    out = {"ok": True}
    out.update(body or {})
    return (status, out)


def _err(exc: OrderError) -> Tuple[int, dict]:
    return (int(getattr(exc, "http_status", 400)),
            {"ok": False, "error": str(exc),
             "code": getattr(exc, "code", "invalid")})


def _dark() -> Tuple[int, dict]:
    return (404, {"ok": False, "error": "Not found.", "code": "not_found"})


def _guard() -> bool:
    return svc.is_enabled()


# ============================================================================
# Buyer order lifecycle
# ============================================================================
def create_order(actor_user_id: Any, payload: Any,
                 *, context: Optional[dict] = None) -> Tuple[int, dict]:
    if not _guard():
        return _dark()
    payload = payload if isinstance(payload, dict) else {}
    product_id = payload.get("product_id")
    quantity = payload.get("quantity", 1)
    if product_id is None:
        return _err(OrderError("product_id is required.", 400, "invalid"))
    try:
        order = svc.create_order(actor_user_id, product_id, quantity=quantity,
                                 context=context)
        return _ok({"order": order}, status=201)
    except OrderError as e:
        return _err(e)


def get_order(actor_user_id: Any, order_id: str) -> Tuple[int, dict]:
    if not _guard():
        return _dark()
    try:
        order = svc.get_order_detail(order_id, requester_user_id=actor_user_id)
        if order is None:
            return (404, {"ok": False, "error": "Not found.", "code": "not_found"})
        return _ok({"order": order})
    except OrderError as e:
        return _err(e)


def list_orders(actor_user_id: Any, *, role: str = "buyer",
                status: Optional[str] = None, limit: int = 200) -> Tuple[int, dict]:
    if not _guard():
        return _dark()
    try:
        if role == "seller":
            rows = svc.list_orders(seller_user_id=actor_user_id, status=status,
                                   limit=limit)
        else:
            rows = svc.list_orders(buyer_user_id=actor_user_id, status=status,
                                   limit=limit)
        return _ok({"orders": rows})
    except OrderError as e:
        return _err(e)


def pay_order(actor_user_id: Any, order_id: str,
              *, context: Optional[dict] = None) -> Tuple[int, dict]:
    if not _guard():
        return _dark()
    try:
        return _ok({"order": svc.pay_order(order_id, actor_user_id, context=context)})
    except OrderError as e:
        return _err(e)


def fulfill_order(actor_user_id: Any, order_id: str, payload: Any,
                  *, context: Optional[dict] = None) -> Tuple[int, dict]:
    if not _guard():
        return _dark()
    payload = payload if isinstance(payload, dict) else {}
    try:
        order = svc.fulfill_order(order_id, actor_user_id,
                                  tracking_ref=payload.get("tracking_ref"),
                                  context=context)
        return _ok({"order": order})
    except OrderError as e:
        return _err(e)


def complete_order(actor_user_id: Any, order_id: str,
                   *, context: Optional[dict] = None) -> Tuple[int, dict]:
    if not _guard():
        return _dark()
    try:
        return _ok({"order": svc.complete_order(order_id, actor_user_id,
                                                context=context)})
    except OrderError as e:
        return _err(e)


def cancel_order(actor_user_id: Any, order_id: str, payload: Any,
                 *, context: Optional[dict] = None) -> Tuple[int, dict]:
    if not _guard():
        return _dark()
    payload = payload if isinstance(payload, dict) else {}
    try:
        order = svc.cancel_order(order_id, actor_user_id,
                                 reason=payload.get("reason"), context=context)
        return _ok({"order": order})
    except OrderError as e:
        return _err(e)


def order_money_summary(actor_user_id: Any, order_id: str) -> Tuple[int, dict]:
    if not _guard():
        return _dark()
    # Ownership-scope the summary: only buyer/seller may see it.
    try:
        if svc.get_order(order_id, requester_user_id=actor_user_id) is None:
            return (404, {"ok": False, "error": "Not found.", "code": "not_found"})
        return _ok({"summary": svc.order_money_summary(order_id)})
    except OrderError as e:
        return _err(e)


# ============================================================================
# Refunds / disputes
# ============================================================================
def open_dispute(actor_user_id: Any, order_id: str, payload: Any) -> Tuple[int, dict]:
    if not _guard():
        return _dark()
    payload = payload if isinstance(payload, dict) else {}
    reason = payload.get("reason")
    if not reason:
        return _err(OrderError("reason is required.", 400, "invalid"))
    try:
        return _ok({"dispute": svc.open_dispute(order_id, actor_user_id,
                                               reason=reason)}, status=201)
    except OrderError as e:
        return _err(e)


def list_refunds(actor_user_id: Any, order_id: str) -> Tuple[int, dict]:
    if not _guard():
        return _dark()
    try:
        if svc.get_order(order_id, requester_user_id=actor_user_id) is None:
            return (404, {"ok": False, "error": "Not found.", "code": "not_found"})
        return _ok({"refunds": svc.list_refunds(order_id)})
    except OrderError as e:
        return _err(e)
