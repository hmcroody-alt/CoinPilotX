"""Business OS — Recommendations: framework-agnostic controller (Stage 6 Part 7).

bot.py owns the raw request, auth (session/token -> user_id) and CSRF; it turns the
returned ``(status, body)`` tuple into a Flask response. All decision logic lives
here so it is unit-testable without Flask.

Contract (mirrors the attribution / crypto / marketplace controllers exactly):

  * every handler returns ``(int status, dict body)``; ``body`` always has an ``ok`` bool;
  * the whole surface is DARK when ``BUSINESS_OS_RECOMMENDATIONS`` is off — every
    handler returns 404;
  * informational only: nothing here moves money or takes an action. A recommendation
    is a suggestion;
  * only curated error codes are surfaced — never an internal exception string;
  * a user's recommendations read is scoped to that user; catalog ingest and recompute
    are operator entry points.
"""

from __future__ import annotations

import os
from typing import Any

from services.business_os.recommendations import schema as _schema
from services.business_os.recommendations import engine as _engine


FLAG_ENV = "BUSINESS_OS_RECOMMENDATIONS"


def is_enabled() -> bool:
    raw = (os.getenv(FLAG_ENV, "") or "").strip().lower()
    return raw in ("1", "true", "on", "yes", "enabled", "canonical")


def _dark():
    return (404, {"ok": False, "error": "Not found."})


def _bad(code: str, msg: str, status: int = 400):
    return (status, {"ok": False, "code": code, "error": msg})


def ensure_ready() -> None:
    """Idempotent schema bootstrap; cheap to call on each request path."""
    _schema.ensure_schema()


# ---------------------------------------------------------------------------
# ingest
# ---------------------------------------------------------------------------
def record_item(payload: Any) -> tuple:
    """Register a recommendable item (operator/catalog-feed entry point)."""
    if not is_enabled():
        return _dark()
    if not isinstance(payload, dict):
        return _bad("missing_payload", "Expected a JSON body.")
    item_id = payload.get("item_id")
    item_type = payload.get("item_type")
    if item_id is None or item_type is None:
        return _bad("missing_fields", "item_id and item_type are required.")
    ensure_ready()
    try:
        result = _engine.record_item(
            item_id, item_type, title=payload.get("title"),
            category=payload.get("category"), tags=payload.get("tags"),
            owner_ref=payload.get("owner_ref"),
            source=(payload.get("source") or "manual"),
            external_ref=payload.get("external_ref"), meta=payload.get("meta"))
    except _engine.RecommendationError as e:
        return _bad("invalid_item", str(e))
    return (200, {"ok": True, "result": result})


def record_interaction(user_id: str, payload: Any) -> tuple:
    if not is_enabled():
        return _dark()
    if not isinstance(payload, dict):
        return _bad("missing_payload", "Expected a JSON body.")
    if not user_id:
        return _bad("unauthenticated", "Sign-in required.", 401)
    item_id = payload.get("item_id")
    interaction_type = payload.get("interaction_type")
    if item_id is None or interaction_type is None:
        return _bad("missing_fields", "item_id and interaction_type are required.")
    ensure_ready()
    try:
        result = _engine.record_interaction(
            str(user_id), item_id, interaction_type,
            weight=(payload.get("weight") if payload.get("weight") is not None else 1),
            occurred_at=payload.get("occurred_at"),
            source=(payload.get("source") or "manual"),
            external_ref=payload.get("external_ref"), meta=payload.get("meta"))
    except _engine.RecommendationError as e:
        return _bad("invalid_interaction", str(e))
    return (200, {"ok": True, "result": result})


# ---------------------------------------------------------------------------
# reporting
# ---------------------------------------------------------------------------
def recommendations_report(user_id: str, model: str = "hybrid",
                           limit: int = 50) -> tuple:
    """The ranked recommendations for the calling user under ``model``. Computes on
    demand if the projection is empty so a first-time caller gets a result."""
    if not is_enabled():
        return _dark()
    if not user_id:
        return _bad("unauthenticated", "Sign-in required.", 401)
    if model not in _engine.VALID_MODELS:
        return _bad("invalid_model", f"unknown model: {model}")
    ensure_ready()
    from services import db
    conn = db.connect()
    try:
        rows = _engine.get_recommendations(str(user_id), model, limit=limit,
                                           conn=conn)
        if not rows:
            # Compute-on-read: build the projection for this user/model once.
            _engine.compute_recommendations(str(user_id), model, limit=limit,
                                            conn=conn)
            conn.commit()
            rows = _engine.get_recommendations(str(user_id), model, limit=limit,
                                               conn=conn)
    finally:
        conn.close()
    return (200, {"ok": True, "result": {"user_id": str(user_id), "model": model,
                                         "recommendations": rows}})


def interactions_report(user_id: str, *, limit: int = 200) -> tuple:
    """The calling user's interaction history."""
    if not is_enabled():
        return _dark()
    if not user_id:
        return _bad("unauthenticated", "Sign-in required.", 401)
    ensure_ready()
    return (200, {"ok": True, "result": {"interactions": _engine.user_interactions(
        str(user_id), limit=int(limit or 200))}})


def popularity_report(limit: int = 100) -> tuple:
    """Operator report: items ranked by global positive engagement."""
    if not is_enabled():
        return _dark()
    ensure_ready()
    return (200, {"ok": True, "result": _engine.item_popularity(limit=int(limit or 100))})


def run_recompute(user_id: str, models=None, limit: int = 50) -> tuple:
    """Operator/cron entry point: recompute a user's recommendations under models."""
    if not is_enabled():
        return _dark()
    if not user_id:
        return _bad("missing_fields", "user_id is required.")
    if models:
        bad = [m for m in models if m not in _engine.VALID_MODELS]
        if bad:
            return _bad("invalid_model", f"unknown model(s): {bad}")
    ensure_ready()
    try:
        result = _engine.recompute_user(str(user_id), models, limit=int(limit or 50))
    except _engine.RecommendationError as e:
        return _bad("invalid_request", str(e))
    return (200, {"ok": True, "result": {"user_id": str(user_id),
                                         "models": list((models or _engine.VALID_MODELS))}})
