"""Business OS — Marketplace: canonical notification emission.

Thin, typed adapters over the EXISTING notification system — this module builds no
competing delivery path, no second queue, no side table. It turns a marketplace
lifecycle fact into a canonical alert and hands it to the orchestrator, which owns
preferences, rate-limiting, channel fan-out, and logging.

Invariants (mirror the advertising vertical):
  * **Never breaks the caller.** Every emit is wrapped so a delivery/import failure
    returns ``{"ok": False, ...}`` instead of raising into order/refund code.
  * **Server-derived content only.** Titles/bodies/deep links are built here from the
    canonical ids the caller passes; no client string is echoed into an alert.
"""

from __future__ import annotations

from typing import Any, Callable, Optional


CATEGORY = "marketplace"


def _order_link(order_id: Any) -> str:
    return f"/marketplace/orders/{order_id}"


# (title, body-template, priority) per canonical event kind.
_EVENTS = {
    "order_paid": ("Order paid", "An order has been paid and is ready to fulfill.", "normal"),
    "order_fulfilled": ("Order shipped", "Your order has been fulfilled.", "normal"),
    "order_completed": ("Order completed", "An order was completed and settled.", "normal"),
    "order_refunded": ("Order refunded", "A refund was issued for an order.", "high"),
    "dispute_opened": ("Dispute opened", "A buyer opened a dispute on an order.", "high"),
    "dispute_resolved": ("Dispute resolved", "A dispute on an order was resolved.", "normal"),
    "seller_restricted": ("Selling restricted", "Your seller account has been restricted.", "high"),
    "seller_reinstated": ("Selling reinstated", "Your seller account has been reinstated.", "normal"),
}


def build_notification(kind: str, order_id: Any = None) -> dict:
    """Pure function: canonical (category/title/body/data/deep_link/priority) for an
    event kind. Unit-testable without delivering anything."""
    title, body, priority = _EVENTS.get(
        kind, ("Marketplace update", "There is an update on your marketplace activity.", "normal"))
    data = {"kind": kind}
    if order_id is not None:
        data["order_id"] = str(order_id)
    return {
        "category": CATEGORY,
        "title": title,
        "body": body,
        "data": data,
        "deep_link": _order_link(order_id) if order_id is not None else "/marketplace",
        "priority": priority,
    }


# The delivery function is swappable so tests can capture emitted alerts without a
# live orchestrator. Default resolves the real orchestrator lazily + defensively.
_sender: Optional[Callable] = None


def set_sender(fn: Optional[Callable]) -> None:
    global _sender
    _sender = fn


def _default_send(user_id: Any, note: dict) -> Any:
    from services import notification_orchestrator as _orch  # lazy, optional
    return _orch.send_user_alert(
        user_id, category=note["category"], title=note["title"],
        body=note["body"], data=note.get("data"), deep_link=note.get("deep_link"),
        priority=note.get("priority", "normal"))


def _emit(user_id: Any, note: dict) -> dict:
    try:
        fn = _sender or _default_send
        fn(user_id, note)
        return {"ok": True, "category": note["category"], "kind": note["data"].get("kind")}
    except Exception as exc:  # never breaks the caller
        return {"ok": False, "error": type(exc).__name__}


def emit_order_event(user_id: Any, kind: str, order_id: Any = None) -> dict:
    return _emit(user_id, build_notification(kind, order_id))
