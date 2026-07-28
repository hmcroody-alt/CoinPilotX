"""Business OS — Business HQ: framework-agnostic HTTP controller (Section 1).

bot.py owns authentication, CSRF, and session identity; it then calls these pure
functions with an *already-authenticated* ``actor_user_id`` and parsed input, and
turns the returned ``(status_code, body)`` tuple into a Flask JSON response. Keeping
the decision logic here (not inline in bot.py) makes every branch unit-testable
without importing Flask (bot.py is not importable in the hermetic sandbox).

Contract for every handler (mirrors the marketplace/advertising controllers):

  * returns ``(int status_code, dict body)``; ``body`` always has an ``ok`` bool;
  * the whole canonical surface is DARK when ``BUSINESS_OS_BUSINESS`` is off — every
    handler returns 404 so no partial canonical path is exposed;
  * access is enforced in the service (no role ⇒ 404, existence not leaked);
  * only the curated ``BusinessError`` message is surfaced — never an internal
    exception string;
  * clients may never set ``business_id`` / ``owner_user_id`` / ``status`` /
    ``version`` / timestamps directly; those are server-authoritative.

``actor_user_id`` is passed in by bot.py from the session/token — never from the
request body. ``context`` carries fresh ``{account_status, access_enabled}`` so
account-hold precedence is evaluated live.
"""

from __future__ import annotations

from typing import Any, Optional, Tuple

from services.business_os.business import service as svc
from services.business_os.business.service import BusinessError


# --- client-writable field allowlists (anything else is ignored) ------------
BUSINESS_CREATE_FIELDS = set(svc.BUSINESS_CREATE_FIELDS)
BUSINESS_UPDATE_FIELDS = set(svc.BUSINESS_UPDATE_FIELDS)
LOCATION_FIELDS = set(svc.LOCATION_FIELDS)


def _ok(body: dict, status: int = 200) -> Tuple[int, dict]:
    out = {"ok": True}
    out.update(body or {})
    return (status, out)


def _err(exc: BusinessError) -> Tuple[int, dict]:
    return (int(getattr(exc, "http_status", 400)),
            {"ok": False, "error": str(exc),
             "code": getattr(exc, "code", "invalid")})


def _dark() -> Tuple[int, dict]:
    """Uniform 404 when the flag is off — no canonical path is revealed."""
    return (404, {"ok": False, "error": "Not found.", "code": "not_found"})


def _pick(payload: Any, allowed: set) -> dict:
    if not isinstance(payload, dict):
        return {}
    return {k: v for k, v in payload.items() if k in allowed}


def _guard() -> bool:
    return svc.is_enabled()


# ============================================================================
# Business identity
# ============================================================================
def create_business(actor_user_id: Any, payload: Any,
                   *, context: Optional[dict] = None) -> Tuple[int, dict]:
    if not _guard():
        return _dark()
    try:
        biz = svc.create_business(actor_user_id, _pick(payload, BUSINESS_CREATE_FIELDS),
                                  context=context)
        return _ok({"business": biz}, status=201)
    except BusinessError as e:
        return _err(e)


def get_business(actor_user_id: Any, business_id: str) -> Tuple[int, dict]:
    if not _guard():
        return _dark()
    try:
        return _ok({"business": svc.get_business(business_id, actor_user_id)})
    except BusinessError as e:
        return _err(e)


def list_businesses(actor_user_id: Any) -> Tuple[int, dict]:
    if not _guard():
        return _dark()
    try:
        return _ok({"businesses": svc.list_businesses(actor_user_id)})
    except BusinessError as e:
        return _err(e)


def update_business(actor_user_id: Any, business_id: str, payload: Any,
                   *, context: Optional[dict] = None) -> Tuple[int, dict]:
    if not _guard():
        return _dark()
    try:
        biz = svc.update_business(business_id, actor_user_id,
                                  _pick(payload, BUSINESS_UPDATE_FIELDS),
                                  context=context)
        return _ok({"business": biz})
    except BusinessError as e:
        return _err(e)


def set_business_status(actor_user_id: Any, business_id: str, payload: Any,
                       *, context: Optional[dict] = None) -> Tuple[int, dict]:
    if not _guard():
        return _dark()
    payload = payload if isinstance(payload, dict) else {}
    action = payload.get("action")
    reason = payload.get("reason")
    try:
        biz = svc.set_business_status(business_id, actor_user_id, action,
                                      reason=reason, context=context)
        return _ok({"business": biz})
    except BusinessError as e:
        return _err(e)


# ============================================================================
# Locations
# ============================================================================
def add_location(actor_user_id: Any, business_id: str, payload: Any,
                *, context: Optional[dict] = None) -> Tuple[int, dict]:
    if not _guard():
        return _dark()
    try:
        loc = svc.add_location(business_id, actor_user_id,
                               _pick(payload, LOCATION_FIELDS), context=context)
        return _ok({"location": loc}, status=201)
    except BusinessError as e:
        return _err(e)


def list_locations(actor_user_id: Any, business_id: str) -> Tuple[int, dict]:
    if not _guard():
        return _dark()
    try:
        return _ok({"locations": svc.list_locations(business_id, actor_user_id)})
    except BusinessError as e:
        return _err(e)


def update_location(actor_user_id: Any, business_id: str, location_id: str,
                   payload: Any, *, context: Optional[dict] = None) -> Tuple[int, dict]:
    if not _guard():
        return _dark()
    try:
        loc = svc.update_location(business_id, actor_user_id, location_id,
                                  _pick(payload, LOCATION_FIELDS), context=context)
        return _ok({"location": loc})
    except BusinessError as e:
        return _err(e)


def close_location(actor_user_id: Any, business_id: str, location_id: str,
                  payload: Any, *, context: Optional[dict] = None) -> Tuple[int, dict]:
    if not _guard():
        return _dark()
    payload = payload if isinstance(payload, dict) else {}
    try:
        loc = svc.close_location(business_id, actor_user_id, location_id,
                                 reason=payload.get("reason"), context=context)
        return _ok({"location": loc})
    except BusinessError as e:
        return _err(e)


# ============================================================================
# Team + RBAC
# ============================================================================
def list_members(actor_user_id: Any, business_id: str) -> Tuple[int, dict]:
    if not _guard():
        return _dark()
    try:
        return _ok({"members": svc.list_members(business_id, actor_user_id)})
    except BusinessError as e:
        return _err(e)


def add_member(actor_user_id: Any, business_id: str, payload: Any,
              *, context: Optional[dict] = None) -> Tuple[int, dict]:
    if not _guard():
        return _dark()
    payload = payload if isinstance(payload, dict) else {}
    member_user_id = payload.get("user_id")
    role = payload.get("role")
    if member_user_id is None or role is None:
        return _err(BusinessError("user_id and role are required.", 400, "invalid"))
    try:
        m = svc.add_member(business_id, actor_user_id, member_user_id, role,
                           context=context)
        return _ok({"member": m}, status=201)
    except BusinessError as e:
        return _err(e)


def update_member_role(actor_user_id: Any, business_id: str, member_user_id: Any,
                      payload: Any, *, context: Optional[dict] = None) -> Tuple[int, dict]:
    if not _guard():
        return _dark()
    payload = payload if isinstance(payload, dict) else {}
    role = payload.get("role")
    if role is None:
        return _err(BusinessError("role is required.", 400, "invalid"))
    try:
        m = svc.update_member_role(business_id, actor_user_id, member_user_id, role,
                                   context=context)
        return _ok({"member": m})
    except BusinessError as e:
        return _err(e)


def remove_member(actor_user_id: Any, business_id: str, member_user_id: Any,
                 payload: Any, *, context: Optional[dict] = None) -> Tuple[int, dict]:
    if not _guard():
        return _dark()
    payload = payload if isinstance(payload, dict) else {}
    try:
        m = svc.remove_member(business_id, actor_user_id, member_user_id,
                              reason=payload.get("reason"), context=context)
        return _ok({"member": m})
    except BusinessError as e:
        return _err(e)


# ============================================================================
# Policies
# ============================================================================
def set_policy(actor_user_id: Any, business_id: str, payload: Any,
              *, context: Optional[dict] = None) -> Tuple[int, dict]:
    if not _guard():
        return _dark()
    payload = payload if isinstance(payload, dict) else {}
    policy_type = payload.get("policy_type")
    body = payload.get("body")
    if policy_type is None:
        return _err(BusinessError("policy_type is required.", 400, "invalid"))
    try:
        p = svc.set_policy(business_id, actor_user_id, policy_type, body,
                           context=context)
        return _ok({"policy": p}, status=201)
    except BusinessError as e:
        return _err(e)


def get_policy(actor_user_id: Any, business_id: str, policy_type: str) -> Tuple[int, dict]:
    if not _guard():
        return _dark()
    try:
        return _ok({"policy": svc.get_policy(business_id, actor_user_id, policy_type)})
    except BusinessError as e:
        return _err(e)


def list_policies(actor_user_id: Any, business_id: str) -> Tuple[int, dict]:
    if not _guard():
        return _dark()
    try:
        return _ok({"policies": svc.list_policies(business_id, actor_user_id)})
    except BusinessError as e:
        return _err(e)


# ============================================================================
# Timeline
# ============================================================================
def get_timeline(actor_user_id: Any, business_id: str,
                *, limit: int = 100) -> Tuple[int, dict]:
    if not _guard():
        return _dark()
    try:
        return _ok({"timeline": svc.get_timeline(business_id, actor_user_id,
                                                 limit=limit)})
    except BusinessError as e:
        return _err(e)
