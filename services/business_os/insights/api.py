"""Business OS — Section 7: Insights framework-agnostic HTTP controller.

Thin ``(status_code, body)`` controller over the unified business-insights service.
bot.py owns auth/CSRF/session and passes an already-authenticated ``actor_user_id``;
this module never trusts identity from the request body.

Contract (mirrors the other Business OS controllers):

  * returns ``(int status_code, dict body)``; ``body`` always carries an ``ok`` bool;
  * DARK when ``BUSINESS_OS_INSIGHTS`` is off — every handler returns 404;
  * access is enforced by the service (no membership ⇒ 404, existence not leaked);
  * only the curated ``InsightsError`` message is surfaced — never a raw exception.

There is no analytics store underneath this domain — every handler resolves to the three
canonical engines through :mod:`services.business_os.insights.service`.
"""

from __future__ import annotations

from typing import Any, Tuple

from services.business_os.insights import service as svc
from services.business_os.insights.service import InsightsError


def _ok(body: dict, status: int = 200) -> Tuple[int, dict]:
    out = {"ok": True}
    out.update(body or {})
    return (status, out)


def _err(exc: InsightsError) -> Tuple[int, dict]:
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
def overview(actor_user_id: Any, business_id: Any, *,
             model: str = None, scope: str = None,
             popularity_limit: Any = 25) -> Tuple[int, dict]:
    if not _guard():
        return _dark()
    try:
        return _ok({"insights": svc.overview(
            business_id, actor_user_id,
            model=model or svc.DEFAULT_ATTR_MODEL,
            attribution_scope=scope or "campaign",
            popularity_limit=_int(popularity_limit, 25))})
    except InsightsError as e:
        return _err(e)


def performance_summary(actor_user_id: Any, business_id: Any) -> Tuple[int, dict]:
    if not _guard():
        return _dark()
    try:
        return _ok({"performance": svc.performance_summary(
            business_id, actor_user_id)})
    except InsightsError as e:
        return _err(e)


def attribution_report(actor_user_id: Any, business_id: Any, *,
                       model: str = None, scope: str = None) -> Tuple[int, dict]:
    if not _guard():
        return _dark()
    try:
        return _ok({"attribution": svc.attribution_report(
            business_id, actor_user_id,
            model=model or svc.DEFAULT_ATTR_MODEL,
            scope=scope or "campaign")})
    except InsightsError as e:
        return _err(e)


def recommendations_popularity(actor_user_id: Any, business_id: Any, *,
                               limit: Any = 100) -> Tuple[int, dict]:
    if not _guard():
        return _dark()
    try:
        return _ok({"recommendations": svc.recommendations_popularity(
            business_id, actor_user_id, limit=_int(limit, 100))})
    except InsightsError as e:
        return _err(e)
