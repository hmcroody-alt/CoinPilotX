"""Business OS — Section 7: Insights service (canonical unified-insights facade).

Flag-gated (``BUSINESS_OS_INSIGHTS``) read surface that UNIFIES the three canonical
analytics engines behind one business-scoped, RBAC-guarded facade:

  * performance  → the business's own metric rollups vs. targets (org_id = business_id);
  * attribution  → platform conversion-credit report (campaign / channel), by model;
  * recommendations → platform item-popularity.

This module owns NO analytics table and recomputes nothing: every number comes from the
engines' own read-only report functions. Who may read a business's insights is resolved
against S1 canonical membership/RBAC (``business.service._effective_role``) — never
re-modeled here. A stranger sees ``not_found`` (existence not leaked); the read never
mutates.
"""

from __future__ import annotations

import os
from typing import Any, Optional

from services import db
from services.business_os.insights import schema as _schema
from services.business_os.business import service as biz_svc
from services.business_os.attribution import engine as _attr
from services.business_os.recommendations import engine as _rec
from services.business_os.performance import engine as _perf


FLAG_ENV = "BUSINESS_OS_INSIGHTS"

# Any active member (viewer+) may read a business's insights.
READ_ROLE = "viewer"

# Valid attribution models mirror the attribution engine's own set.
ATTR_MODELS = _attr.VALID_MODELS
DEFAULT_ATTR_MODEL = "last_touch"
ATTR_SCOPES = ("campaign", "channel")


class InsightsError(ValueError):
    """One stable domain-facing error carrying an HTTP status + machine code."""

    def __init__(self, message: str, http_status: int = 400, code: str = "invalid"):
        super().__init__(message)
        self.http_status = int(http_status)
        self.code = code


# ---------------------------------------------------------------------------
def is_enabled() -> bool:
    raw = (os.environ.get(FLAG_ENV) or "").strip().lower()
    return raw in {"1", "true", "on", "yes", "enabled", "canonical"}


def _require_enabled() -> None:
    if not is_enabled():
        raise InsightsError("Insights is not enabled in this environment.",
                            503, "disabled")


def _sid(user_id: Any) -> str:
    return biz_svc._sid(user_id)


# ---------------------------------------------------------------------------
# Authorization — reuse S1 canonical RBAC verbatim; existence is never leaked.
# ---------------------------------------------------------------------------
def _business_exists(conn, business_id: Any) -> bool:
    row = conn.execute(
        "SELECT 1 FROM business_os_business WHERE business_id = ? LIMIT 1",
        (str(business_id),),
    ).fetchone()
    return row is not None


def _authorized_reader(conn, business_id: Any, user_id: Any) -> bool:
    """True iff the caller is an active member of the business with read rank (viewer+)."""
    role = biz_svc._effective_role(conn, str(business_id), user_id)
    if role is None:
        return False
    return biz_svc._role_rank(role) <= biz_svc._role_rank(READ_ROLE)


def _require_reader(business_id: Any, user_id: Any) -> None:
    """Raise 404 not_found when the business is missing OR the caller has no read access —
    the two are indistinguishable to the caller (existence not leaked)."""
    if not business_id:
        raise InsightsError("Not found.", 404, "not_found")
    conn = db.connect()
    try:
        if not _business_exists(conn, business_id):
            raise InsightsError("Not found.", 404, "not_found")
        if not _authorized_reader(conn, business_id, user_id):
            raise InsightsError("Not found.", 404, "not_found")
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Public read facade
# ---------------------------------------------------------------------------
def performance_summary(business_id: Any, actor_user_id: Any) -> dict:
    """The business's metric rollups vs. targets (read-only). org_id == business_id."""
    _require_enabled()
    _require_reader(business_id, actor_user_id)
    summaries = _perf.get_summaries(str(business_id))
    targets = _perf.list_targets(str(business_id))
    return {"business_id": str(business_id),
            "summaries": summaries or [],
            "targets": targets or []}


def attribution_report(business_id: Any, actor_user_id: Any, *,
                       model: str = DEFAULT_ATTR_MODEL,
                       scope: str = "campaign") -> dict:
    """Platform conversion-credit report by model, scoped to campaign or channel."""
    _require_enabled()
    _require_reader(business_id, actor_user_id)
    model = (model or DEFAULT_ATTR_MODEL)
    if model not in ATTR_MODELS:
        raise InsightsError(
            "Unknown attribution model: %s" % model, 400, "invalid_model")
    scope = (scope or "campaign")
    if scope not in ATTR_SCOPES:
        raise InsightsError("Unknown scope: %s" % scope, 400, "invalid_scope")
    report = (_attr.campaign_report(model) if scope == "campaign"
              else _attr.channel_report(model))
    return {"business_id": str(business_id), "scope": scope, "report": report}


def recommendations_popularity(business_id: Any, actor_user_id: Any, *,
                               limit: int = 100) -> dict:
    """Platform item-popularity read (top items by interaction)."""
    _require_enabled()
    _require_reader(business_id, actor_user_id)
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = 100
    limit = max(1, min(limit, 500))
    return {"business_id": str(business_id),
            "popularity": _rec.item_popularity(limit=limit)}


def overview(business_id: Any, actor_user_id: Any, *,
             model: str = DEFAULT_ATTR_MODEL,
             attribution_scope: str = "campaign",
             popularity_limit: int = 25) -> dict:
    """One unified, authorized read across all three engines. Authorization is checked
    once; the engine reads are stitched into a single insights overview."""
    _require_enabled()
    _require_reader(business_id, actor_user_id)
    model = model if model in ATTR_MODELS else DEFAULT_ATTR_MODEL
    scope = attribution_scope if attribution_scope in ATTR_SCOPES else "campaign"
    try:
        plimit = max(1, min(int(popularity_limit), 500))
    except (TypeError, ValueError):
        plimit = 25

    perf_summaries = _perf.get_summaries(str(business_id)) or []
    attr = (_attr.campaign_report(model) if scope == "campaign"
            else _attr.channel_report(model))
    pop = _rec.item_popularity(limit=plimit)

    return {
        "business_id": str(business_id),
        "performance": {"summaries": perf_summaries,
                        "count": len(perf_summaries)},
        "attribution": {"model": model, "scope": scope, "report": attr},
        "recommendations": {"popularity": pop},
    }
