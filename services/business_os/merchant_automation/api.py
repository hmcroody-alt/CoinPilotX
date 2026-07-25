"""Business OS — Merchant automation: framework-agnostic controller (Stage 6 Part 11).

bot.py owns the raw request, auth (session/token -> user_id) and CSRF; it turns the
returned ``(status, body)`` tuple into a Flask response. All decision logic lives here
so it is unit-testable without Flask.

Contract (mirrors the attribution / recommendations / crypto controllers exactly):

  * every handler returns ``(int status, dict body)``; ``body`` always has an ``ok`` bool;
  * the whole surface is DARK when ``BUSINESS_OS_MERCHANT_AUTOMATION`` is off — every
    handler returns 404;
  * informational only: nothing here moves money or takes an action. A proposal is a
    suggestion;
  * only curated error codes are surfaced — never an internal exception string.
"""

from __future__ import annotations

import os
from typing import Any

from services.business_os.merchant_automation import schema as _schema
from services.business_os.merchant_automation import engine as _engine


FLAG_ENV = "BUSINESS_OS_MERCHANT_AUTOMATION"


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
def record_rule(payload: Any) -> tuple:
    """Declare a merchant rule (operator/merchant entry point)."""
    if not is_enabled():
        return _dark()
    if not isinstance(payload, dict):
        return _bad("missing_payload", "Expected a JSON body.")
    merchant_id = payload.get("merchant_id")
    signal_type = payload.get("signal_type")
    operator = payload.get("operator")
    threshold = payload.get("threshold")
    action_type = payload.get("action_type")
    if (merchant_id is None or signal_type is None or operator is None
            or threshold is None or action_type is None):
        return _bad("missing_fields",
                    "merchant_id, signal_type, operator, threshold and action_type "
                    "are required.")
    ensure_ready()
    try:
        result = _engine.record_rule(
            merchant_id, signal_type, operator, threshold, action_type,
            name=payload.get("name"),
            active=(payload.get("active") if payload.get("active") is not None else True),
            priority=(payload.get("priority") if payload.get("priority") is not None else 0),
            source=(payload.get("source") or "manual"),
            external_ref=payload.get("external_ref"), meta=payload.get("meta"))
    except _engine.MerchantAutomationError as e:
        return _bad("invalid_rule", str(e))
    return (200, {"ok": True, "result": result})


def record_signal(payload: Any) -> tuple:
    """Append a signal fact (feed/merchant entry point)."""
    if not is_enabled():
        return _dark()
    if not isinstance(payload, dict):
        return _bad("missing_payload", "Expected a JSON body.")
    merchant_id = payload.get("merchant_id")
    subject_ref = payload.get("subject_ref")
    signal_type = payload.get("signal_type")
    value = payload.get("value")
    if (merchant_id is None or subject_ref is None or signal_type is None
            or value is None):
        return _bad("missing_fields",
                    "merchant_id, subject_ref, signal_type and value are required.")
    ensure_ready()
    try:
        result = _engine.record_signal(
            merchant_id, subject_ref, signal_type, value,
            observed_at=payload.get("observed_at"),
            source=(payload.get("source") or "manual"),
            external_ref=payload.get("external_ref"), meta=payload.get("meta"))
    except _engine.MerchantAutomationError as e:
        return _bad("invalid_signal", str(e))
    return (200, {"ok": True, "result": result})


# ---------------------------------------------------------------------------
# evaluation + reporting
# ---------------------------------------------------------------------------
def proposals_report(merchant_id: str, limit: int = 200) -> tuple:
    """The proposed actions for a merchant. Computes on demand if the projection is
    empty so a first-time caller gets a result. Read-only; nothing is executed."""
    if not is_enabled():
        return _dark()
    merchant_id = str(merchant_id or "").strip()
    if not merchant_id:
        return _bad("missing_fields", "merchant_id is required.")
    ensure_ready()
    from services import db
    conn = db.connect()
    try:
        rows = _engine.get_proposals(merchant_id, limit=int(limit or 200), conn=conn)
        if not rows:
            _engine.evaluate_merchant(merchant_id, conn=conn)
            conn.commit()
            rows = _engine.get_proposals(merchant_id, limit=int(limit or 200),
                                         conn=conn)
    finally:
        conn.close()
    return (200, {"ok": True, "result": {"merchant_id": merchant_id,
                                         "proposals": rows}})


def rules_report(merchant_id: str, limit: int = 200) -> tuple:
    """The declared rules for a merchant."""
    if not is_enabled():
        return _dark()
    merchant_id = str(merchant_id or "").strip()
    if not merchant_id:
        return _bad("missing_fields", "merchant_id is required.")
    ensure_ready()
    return (200, {"ok": True, "result": {"merchant_id": merchant_id,
                                         "rules": _engine.list_rules(
                                             merchant_id, limit=int(limit or 200))}})


def signals_report(merchant_id: str, limit: int = 500) -> tuple:
    """The current (latest-per-key) signal state for a merchant."""
    if not is_enabled():
        return _dark()
    merchant_id = str(merchant_id or "").strip()
    if not merchant_id:
        return _bad("missing_fields", "merchant_id is required.")
    ensure_ready()
    return (200, {"ok": True, "result": {"merchant_id": merchant_id,
                                         "signals": _engine.current_signals(
                                             merchant_id, limit=int(limit or 500))}})


def run_evaluate(merchant_id: str) -> tuple:
    """Operator/cron entry point: re-evaluate a merchant's rules against latest signals
    and rebuild the proposal projection. Nothing is executed — proposals are
    suggestions."""
    if not is_enabled():
        return _dark()
    merchant_id = str(merchant_id or "").strip()
    if not merchant_id:
        return _bad("missing_fields", "merchant_id is required.")
    ensure_ready()
    try:
        result = _engine.evaluate_merchant(merchant_id)
    except _engine.MerchantAutomationError as e:
        return _bad("invalid_request", str(e))
    return (200, {"ok": True, "result": result})
