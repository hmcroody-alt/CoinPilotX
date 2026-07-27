"""Business OS — Crypto intelligence: framework-agnostic controller (Stage 5 Part 5).

bot.py owns the raw request, auth (session/token -> user_id) and CSRF; it turns the
returned ``(status, body)`` tuple into a Flask response. All decision logic lives
here so it is unit-testable without Flask.

Contract (mirrors the marketplace / advertising / IAP controllers exactly):

  * every handler returns ``(int status, dict body)``; ``body`` always has an ``ok`` bool;
  * the whole surface is DARK when ``BUSINESS_OS_CRYPTO`` is off — every handler returns 404;
  * informational only: nothing here executes a trade, transfer, or custody action.
    Recording a transaction is bookkeeping of something that already happened
    elsewhere; it never moves money;
  * only curated error messages are surfaced — never an internal exception string.

The market ``price_lookup`` is injectable so tests pass a stub and production wires
the unified market service; when not injected the controller builds it from the
unified ``market`` module (a resilient read over the three existing providers).
"""

from __future__ import annotations

import os
from typing import Any, Optional

from services.business_os.crypto import schema as _schema
from services.business_os.crypto import engine as _engine
from services.business_os.crypto import alerts as _alerts
from services.business_os.crypto import market as _market


FLAG_ENV = "BUSINESS_OS_CRYPTO"


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


def _default_price_lookup():
    return _market.price_cents_lookup()


# ---------------------------------------------------------------------------
# transactions + portfolio
# ---------------------------------------------------------------------------
def record_transaction(user_id: str, payload: Any) -> tuple:
    """Record a buy/sell the user (or a connected read-only feed) reports. Pure
    bookkeeping — never an order."""
    if not is_enabled():
        return _dark()
    if not isinstance(payload, dict):
        return _bad("missing_payload", "Expected a JSON body.")
    if not user_id:
        return _bad("unauthenticated", "Sign-in required.", 401)
    symbol = payload.get("symbol")
    side = payload.get("side")
    quantity = payload.get("quantity")
    unit_price_cents = payload.get("unit_price_cents")
    if symbol is None or side is None or quantity is None or unit_price_cents is None:
        return _bad("missing_fields",
                    "symbol, side, quantity, unit_price_cents are required.")
    try:
        unit_price_cents = int(unit_price_cents)
        fee_cents = int(payload.get("fee_cents") or 0)
    except (TypeError, ValueError):
        return _bad("bad_money", "Money fields must be integer cents.")
    ensure_ready()
    try:
        result = _engine.record_transaction(
            str(user_id), symbol, side, quantity, unit_price_cents,
            fee_cents=fee_cents,
            executed_at=payload.get("executed_at"),
            source=(payload.get("source") or "manual"),
            external_ref=payload.get("external_ref"),
            notes=payload.get("notes"),
            method=(payload.get("method") or "fifo"))
    except _engine.CryptoEngineError as e:
        return _bad("invalid_transaction", str(e))
    return (200, {"ok": True, "result": result})


def portfolio(user_id: str, *, price_lookup=None) -> tuple:
    """Return the user's holdings with realized + unrealized P&L."""
    if not is_enabled():
        return _dark()
    if not user_id:
        return _bad("unauthenticated", "Sign-in required.", 401)
    ensure_ready()
    lookup = price_lookup if price_lookup is not None else _default_price_lookup()
    summary = _engine.portfolio_summary(str(user_id), price_lookup=lookup)
    return (200, {"ok": True, "result": summary})


# ---------------------------------------------------------------------------
# alerts
# ---------------------------------------------------------------------------
def create_alert(user_id: str, payload: Any) -> tuple:
    if not is_enabled():
        return _dark()
    if not isinstance(payload, dict):
        return _bad("missing_payload", "Expected a JSON body.")
    if not user_id:
        return _bad("unauthenticated", "Sign-in required.", 401)
    symbol = payload.get("symbol")
    comparator = payload.get("comparator")
    threshold = payload.get("threshold")
    if symbol is None or comparator is None or threshold is None:
        return _bad("missing_fields",
                    "symbol, comparator, threshold are required.")
    ensure_ready()
    try:
        result = _alerts.create_alert(
            str(user_id), symbol, comparator, threshold,
            metric=(payload.get("metric") or "price_usd"),
            repeat_mode=(payload.get("repeat_mode") or "once"),
            cooldown_seconds=int(payload.get("cooldown_seconds") or 0),
            note=payload.get("note"))
    except _alerts.CryptoAlertError as e:
        return _bad("invalid_alert", str(e))
    return (200, {"ok": True, "result": result})


def list_alerts(user_id: str, *, active_only: bool = False) -> tuple:
    if not is_enabled():
        return _dark()
    if not user_id:
        return _bad("unauthenticated", "Sign-in required.", 401)
    ensure_ready()
    return (200, {"ok": True, "result": _alerts.list_alerts(
        str(user_id), active_only=active_only)})


def delete_alert(user_id: str, alert_id: str) -> tuple:
    if not is_enabled():
        return _dark()
    if not user_id:
        return _bad("unauthenticated", "Sign-in required.", 401)
    if not alert_id:
        return _bad("missing_fields", "alert_id is required.")
    ensure_ready()
    # Scope the deactivation to alerts the caller owns.
    owned = [a["alert_id"] for a in _alerts.list_alerts(str(user_id))]
    if alert_id not in owned:
        return _bad("not_found", "No such alert.", 404)
    ok = _alerts.deactivate_alert(alert_id)
    return (200, {"ok": True, "result": {"deactivated": bool(ok)}})


def run_sweep(*, price_lookup=None) -> tuple:
    """Operator/cron entry point: evaluate all active alerts. Not user-scoped."""
    if not is_enabled():
        return _dark()
    ensure_ready()
    lookup = price_lookup if price_lookup is not None else _default_price_lookup()
    return (200, {"ok": True, "result": _alerts.sweep(lookup)})
