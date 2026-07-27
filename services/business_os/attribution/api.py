"""Business OS — Attribution: framework-agnostic controller (Stage 6 Part 3).

bot.py owns the raw request, auth (session/token -> user_id) and CSRF; it turns the
returned ``(status, body)`` tuple into a Flask response. All decision logic lives
here so it is unit-testable without Flask.

Contract (mirrors the crypto / marketplace / advertising controllers exactly):

  * every handler returns ``(int status, dict body)``; ``body`` always has an ``ok`` bool;
  * the whole surface is DARK when ``BUSINESS_OS_ATTRIBUTION`` is off — every handler
    returns 404;
  * informational only: nothing here moves money. Credit is a reporting quantity;
  * only curated error codes are surfaced — never an internal exception string;
  * user-owned reads (a conversion's credit path) are scoped to the owning user;
    cross-org aggregate reports and recompute are operator entry points.
"""

from __future__ import annotations

import os
from typing import Any

from services.business_os.attribution import schema as _schema
from services.business_os.attribution import engine as _engine


FLAG_ENV = "BUSINESS_OS_ATTRIBUTION"


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
def record_touchpoint(user_id: str, payload: Any) -> tuple:
    if not is_enabled():
        return _dark()
    if not isinstance(payload, dict):
        return _bad("missing_payload", "Expected a JSON body.")
    if not user_id:
        return _bad("unauthenticated", "Sign-in required.", 401)
    channel = payload.get("channel")
    touch_type = payload.get("touch_type")
    if channel is None or touch_type is None:
        return _bad("missing_fields", "channel and touch_type are required.")
    ensure_ready()
    try:
        result = _engine.record_touchpoint(
            str(user_id), channel, touch_type,
            campaign_ref=payload.get("campaign_ref"),
            occurred_at=payload.get("occurred_at"),
            source=(payload.get("source") or "manual"),
            external_ref=payload.get("external_ref"),
            meta=payload.get("meta"))
    except _engine.AttributionError as e:
        return _bad("invalid_touchpoint", str(e))
    return (200, {"ok": True, "result": result})


def record_conversion(user_id: str, payload: Any) -> tuple:
    if not is_enabled():
        return _dark()
    if not isinstance(payload, dict):
        return _bad("missing_payload", "Expected a JSON body.")
    if not user_id:
        return _bad("unauthenticated", "Sign-in required.", 401)
    conversion_type = payload.get("conversion_type")
    value_cents = payload.get("value_cents")
    if conversion_type is None or value_cents is None:
        return _bad("missing_fields", "conversion_type and value_cents are required.")
    ensure_ready()
    try:
        result = _engine.record_conversion(
            str(user_id), conversion_type, value_cents,
            currency=(payload.get("currency") or "usd"),
            occurred_at=payload.get("occurred_at"),
            lookback_days=(payload.get("lookback_days") or 30),
            source=(payload.get("source") or "manual"),
            external_ref=payload.get("external_ref"),
            related_object=payload.get("related_object"),
            meta=payload.get("meta"))
    except _engine.AttributionError as e:
        return _bad("invalid_conversion", str(e))
    # Auto-compute the requested (or default) model so the caller gets an immediate
    # attributed result without a separate recompute round-trip.
    model = (payload.get("model") or "last_touch")
    if model not in _engine.VALID_MODELS:
        return _bad("invalid_model", f"unknown model: {model}")
    if result.get("recorded"):
        try:
            credit = _engine.compute_credits(result["conversion_id"], model)
            result["attribution"] = credit
        except _engine.AttributionError:
            pass
    return (200, {"ok": True, "result": result})


# ---------------------------------------------------------------------------
# reporting
# ---------------------------------------------------------------------------
def conversion_report(user_id: str, conversion_id: str,
                      model: str = "last_touch") -> tuple:
    """The credit path for one conversion, scoped to the owning user."""
    if not is_enabled():
        return _dark()
    if not user_id:
        return _bad("unauthenticated", "Sign-in required.", 401)
    if not conversion_id:
        return _bad("missing_fields", "conversion_id is required.")
    if model not in _engine.VALID_MODELS:
        return _bad("invalid_model", f"unknown model: {model}")
    ensure_ready()
    from services import db
    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT user_id FROM business_os_attr_conversions WHERE conversion_id = ?",
            (conversion_id,)).fetchone()
        if row is None or str(row["user_id"]) != str(user_id):
            return _bad("not_found", "No such conversion.", 404)
        credits = _engine.conversion_credits(conversion_id, model, conn=conn)
    finally:
        conn.close()
    return (200, {"ok": True, "result": {"conversion_id": conversion_id,
                                         "model": model, "credits": credits}})


def path_report(user_id: str, *, limit: int = 200) -> tuple:
    """The ordered touchpoint path for the calling user."""
    if not is_enabled():
        return _dark()
    if not user_id:
        return _bad("unauthenticated", "Sign-in required.", 401)
    ensure_ready()
    return (200, {"ok": True, "result": {"path": _engine.user_path(
        str(user_id), limit=int(limit or 200))}})


def campaign_report(model: str = "last_touch") -> tuple:
    """Operator report: credit aggregated by campaign under ``model``."""
    if not is_enabled():
        return _dark()
    if model not in _engine.VALID_MODELS:
        return _bad("invalid_model", f"unknown model: {model}")
    ensure_ready()
    return (200, {"ok": True, "result": _engine.campaign_report(model)})


def run_recompute(conversion_id: str, models=None) -> tuple:
    """Operator/cron entry point: recompute one conversion under given models."""
    if not is_enabled():
        return _dark()
    if not conversion_id:
        return _bad("missing_fields", "conversion_id is required.")
    if models:
        bad = [m for m in models if m not in _engine.VALID_MODELS]
        if bad:
            return _bad("invalid_model", f"unknown model(s): {bad}")
    ensure_ready()
    try:
        return (200, {"ok": True, "result": _engine.recompute_conversion(
            conversion_id, models)})
    except _engine.AttributionError as e:
        return _bad("not_found", str(e), 404)
