"""Business OS — Section 9: Events framework-agnostic HTTP controller.

Thin ``(status_code, body)`` controller over the canonical events service. bot.py owns
auth/CSRF/session and passes an already-authenticated ``actor_user_id``; this module never
trusts identity from the request body.

Contract (mirrors the other Business OS controllers):

  * returns ``(int status_code, dict body)``; ``body`` always carries an ``ok`` bool;
  * DARK when ``BUSINESS_OS_EVENTS`` is off — every handler returns 404;
  * access is enforced by the service (no membership ⇒ 404, existence not leaked);
  * only the curated ``EventError`` message is surfaced — never a raw exception.
"""

from __future__ import annotations

from typing import Any, Optional, Tuple

from services.business_os.events import service as svc
from services.business_os.events.service import EventError


def _ok(body: dict, status: int = 200) -> Tuple[int, dict]:
    out = {"ok": True}
    out.update(body or {})
    return (status, out)


def _err(exc: EventError) -> Tuple[int, dict]:
    return (int(getattr(exc, "http_status", 400)),
            {"ok": False, "error": str(exc),
             "code": getattr(exc, "code", "invalid")})


def _dark() -> Tuple[int, dict]:
    return (404, {"ok": False, "error": "Not found.", "code": "not_found"})


def _guard() -> bool:
    return svc.is_enabled()


def _int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


# ============================================================================
# Event lifecycle (management)
# ============================================================================
def create_event(actor_user_id: Any, business_id: Any, payload: dict, *,
                  context: Optional[dict] = None) -> Tuple[int, dict]:
    if not _guard():
        return _dark()
    try:
        return _ok({"event": svc.create_event(
            business_id, actor_user_id, payload or {}, context=context)}, 201)
    except EventError as e:
        return _err(e)


def add_ticket_type(actor_user_id: Any, event_id: Any, payload: dict, *,
                    context: Optional[dict] = None) -> Tuple[int, dict]:
    if not _guard():
        return _dark()
    try:
        return _ok({"ticket_type": svc.add_ticket_type(
            event_id, actor_user_id, payload or {}, context=context)}, 201)
    except EventError as e:
        return _err(e)


def publish_event(actor_user_id: Any, event_id: Any, *,
                  context: Optional[dict] = None) -> Tuple[int, dict]:
    if not _guard():
        return _dark()
    try:
        return _ok({"event": svc.publish_event(
            event_id, actor_user_id, context=context)})
    except EventError as e:
        return _err(e)


def cancel_event(actor_user_id: Any, event_id: Any, *,
                 context: Optional[dict] = None) -> Tuple[int, dict]:
    if not _guard():
        return _dark()
    try:
        return _ok({"event": svc.cancel_event(
            event_id, actor_user_id, context=context)})
    except EventError as e:
        return _err(e)


# ============================================================================
# Reads
# ============================================================================
def get_event(actor_user_id: Any, event_id: Any) -> Tuple[int, dict]:
    if not _guard():
        return _dark()
    try:
        event = svc.get_event(event_id, actor_user_id)
    except EventError as e:
        return _err(e)
    if event is None:
        return _dark()
    return _ok({"event": event})


def list_business_events(actor_user_id: Any, business_id: Any, *,
                         limit: Any = 100) -> Tuple[int, dict]:
    if not _guard():
        return _dark()
    try:
        return _ok({"events": svc.list_business_events(
            business_id, actor_user_id, limit=_int(limit, 100))})
    except EventError as e:
        return _err(e)


def my_tickets(actor_user_id: Any, *, limit: Any = 200) -> Tuple[int, dict]:
    if not _guard():
        return _dark()
    try:
        return _ok({"tickets": svc.list_my_tickets(
            actor_user_id, limit=_int(limit, 200))})
    except EventError as e:
        return _err(e)


# ============================================================================
# Ticketing
# ============================================================================
def purchase_ticket(actor_user_id: Any, event_id: Any, ticket_type_id: Any, *,
                    client_ref: Optional[str] = None,
                    context: Optional[dict] = None) -> Tuple[int, dict]:
    if not _guard():
        return _dark()
    try:
        ticket = svc.purchase_ticket(
            event_id, ticket_type_id, actor_user_id,
            client_ref=client_ref, context=context)
        status = 200 if ticket.get("idempotent") else 201
        return _ok({"ticket": ticket}, status)
    except EventError as e:
        return _err(e)


def check_in_ticket(actor_user_id: Any, ticket_id: Any, *,
                    context: Optional[dict] = None) -> Tuple[int, dict]:
    if not _guard():
        return _dark()
    try:
        return _ok({"ticket": svc.check_in_ticket(
            ticket_id, actor_user_id, context=context)})
    except EventError as e:
        return _err(e)


def refund_ticket(actor_user_id: Any, ticket_id: Any, *,
                  context: Optional[dict] = None) -> Tuple[int, dict]:
    if not _guard():
        return _dark()
    try:
        return _ok({"ticket": svc.refund_ticket(
            ticket_id, actor_user_id, context=context)})
    except EventError as e:
        return _err(e)


# ============================================================================
# Settlement + summary
# ============================================================================
def settle_event(actor_user_id: Any, event_id: Any, *,
                 context: Optional[dict] = None) -> Tuple[int, dict]:
    if not _guard():
        return _dark()
    try:
        return _ok({"settlement": svc.settle_event(
            event_id, actor_user_id, context=context)})
    except EventError as e:
        return _err(e)


def event_summary(actor_user_id: Any, event_id: Any) -> Tuple[int, dict]:
    if not _guard():
        return _dark()
    try:
        return _ok({"summary": svc.event_summary(event_id, actor_user_id)})
    except EventError as e:
        return _err(e)
