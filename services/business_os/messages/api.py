"""Business OS — Section 6: Messages framework-agnostic HTTP controller.

Thin ``(status_code, body)`` controller over the canonical business-messaging service.
bot.py owns auth/CSRF/session and passes an already-authenticated ``actor_user_id``;
this module never trusts identity from the request body.

Contract (mirrors the other Business OS controllers):

  * returns ``(int status_code, dict body)``; ``body`` always carries an ``ok`` bool;
  * DARK when ``BUSINESS_OS_MESSAGES`` is off — every handler returns 404;
  * ownership + membership rules are enforced by the service (no access ⇒ 404,
    existence not leaked; insufficient role ⇒ 403);
  * only the curated ``MessageError`` message is surfaced — never a raw exception.

There is no second message system underneath — every handler resolves to the one
canonical ``pulse_messages`` engine through
:mod:`services.business_os.messages.service`.
"""

from __future__ import annotations

from typing import Any, Optional, Tuple

from services.business_os.messages import service as svc
from services.business_os.messages.service import MessageError


def _ok(body: dict, status: int = 200) -> Tuple[int, dict]:
    out = {"ok": True}
    out.update(body or {})
    return (status, out)


def _err(exc: MessageError) -> Tuple[int, dict]:
    return (int(getattr(exc, "http_status", 400)),
            {"ok": False, "error": str(exc),
             "code": getattr(exc, "code", "invalid")})


def _dark() -> Tuple[int, dict]:
    return (404, {"ok": False, "error": "Not found.", "code": "not_found"})


def _guard() -> bool:
    return svc.is_enabled()


# ============================================================================
# Threads
# ============================================================================
def start_thread(actor_user_id: Any, business_id: Any, payload: Any,
                 *, context: Optional[dict] = None) -> Tuple[int, dict]:
    if not _guard():
        return _dark()
    payload = payload if isinstance(payload, dict) else {}
    customer_user_id = payload.get("customer_user_id", actor_user_id)
    try:
        thread = svc.start_business_thread(
            business_id, customer_user_id, actor_user_id,
            subject=payload.get("subject"), context=context)
        return _ok({"thread": thread}, status=201)
    except MessageError as e:
        return _err(e)


def business_inbox(actor_user_id: Any, business_id: Any,
                   *, limit: int = 100) -> Tuple[int, dict]:
    if not _guard():
        return _dark()
    try:
        return _ok({"threads": svc.list_business_inbox(
            business_id, actor_user_id, limit=limit)})
    except MessageError as e:
        return _err(e)


def my_threads(actor_user_id: Any, *, limit: int = 100) -> Tuple[int, dict]:
    if not _guard():
        return _dark()
    try:
        return _ok({"threads": svc.list_customer_threads(
            actor_user_id, limit=limit)})
    except MessageError as e:
        return _err(e)


def get_thread(actor_user_id: Any, conversation_id: Any) -> Tuple[int, dict]:
    if not _guard():
        return _dark()
    try:
        thread = svc.get_thread(conversation_id, actor_user_id)
        if thread is None:
            return (404, {"ok": False, "error": "Not found.", "code": "not_found"})
        return _ok({"thread": thread})
    except MessageError as e:
        return _err(e)


def list_messages(actor_user_id: Any, conversation_id: Any, *,
                  limit: int = 50, before_id: Any = None) -> Tuple[int, dict]:
    if not _guard():
        return _dark()
    try:
        msgs = svc.list_thread_messages(conversation_id, actor_user_id,
                                        limit=limit, before_id=before_id)
        if msgs is None:
            return (404, {"ok": False, "error": "Not found.", "code": "not_found"})
        return _ok({"messages": msgs})
    except MessageError as e:
        return _err(e)


def send_message(actor_user_id: Any, conversation_id: Any, payload: Any,
                 *, context: Optional[dict] = None) -> Tuple[int, dict]:
    if not _guard():
        return _dark()
    payload = payload if isinstance(payload, dict) else {}
    try:
        msg = svc.send_message(
            conversation_id, actor_user_id, payload.get("body"),
            message_type=payload.get("message_type", "text"),
            media_url=payload.get("media_url"),
            reply_to_id=payload.get("reply_to_id", 0),
            client_message_id=payload.get("client_message_id"),
            context=context)
        # A brand-new message is 201; an idempotent replay is 200.
        return _ok({"message": msg}, status=200 if msg.get("idempotent") else 201)
    except MessageError as e:
        return _err(e)


def mark_read(actor_user_id: Any, conversation_id: Any) -> Tuple[int, dict]:
    if not _guard():
        return _dark()
    try:
        return _ok(svc.mark_read(conversation_id, actor_user_id))
    except MessageError as e:
        return _err(e)


def report_message(actor_user_id: Any, conversation_id: Any, payload: Any) -> Tuple[int, dict]:
    if not _guard():
        return _dark()
    payload = payload if isinstance(payload, dict) else {}
    message_id = payload.get("message_id")
    if message_id is None:
        return _err(MessageError("message_id is required.", 400, "invalid"))
    try:
        return _ok({"report": svc.report_message(
            conversation_id, message_id, actor_user_id,
            reason=payload.get("reason"))}, status=201)
    except MessageError as e:
        return _err(e)
