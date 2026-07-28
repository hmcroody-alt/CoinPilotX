"""Business OS — Section 10: Verification framework-agnostic HTTP controller.

Thin ``(status_code, body)`` controller over the cross-domain verification service. bot.py
owns auth/CSRF/session and passes an already-authenticated ``actor_user_id``; this module
never trusts identity from the request body.

Contract (mirrors the other Business OS controllers):

  * returns ``(int status_code, dict body)``; ``body`` always carries an ``ok`` bool;
  * DARK when ``BUSINESS_OS_VERIFICATION`` is off — every handler returns 404;
  * access is enforced by the service (no membership ⇒ 404, existence not leaked);
  * only the curated ``VerificationError`` message is surfaced — never a raw exception.
"""

from __future__ import annotations

from typing import Any, Optional, Tuple

from services.business_os.verification import service as svc
from services.business_os.verification.service import VerificationError


def _ok(body: dict, status: int = 200) -> Tuple[int, dict]:
    out = {"ok": True}
    out.update(body or {})
    return (status, out)


def _err(exc: VerificationError) -> Tuple[int, dict]:
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
def run_verification(actor_user_id: Any, business_id: Any, *,
                     context: Optional[dict] = None) -> Tuple[int, dict]:
    if not _guard():
        return _dark()
    try:
        return _ok({"run": svc.run_verification(
            business_id, actor_user_id, context=context)}, 201)
    except VerificationError as e:
        return _err(e)


def get_run(actor_user_id: Any, run_id: Any) -> Tuple[int, dict]:
    if not _guard():
        return _dark()
    try:
        return _ok({"run": svc.get_run(run_id, actor_user_id)})
    except VerificationError as e:
        return _err(e)


def list_runs(actor_user_id: Any, business_id: Any, *,
              limit: Any = 50) -> Tuple[int, dict]:
    if not _guard():
        return _dark()
    try:
        return _ok({"runs": svc.list_runs(
            business_id, actor_user_id, limit=_int(limit, 50))})
    except VerificationError as e:
        return _err(e)
