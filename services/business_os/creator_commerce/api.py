"""Business OS — Creator commerce: framework-agnostic controller (Stage 6 Part 15).

bot.py owns the raw request, auth (session/token -> user_id) and CSRF; it turns the
returned ``(status, body)`` tuple into a Flask response. All decision logic lives here
so it is unit-testable without Flask.

Contract (mirrors the attribution / recommendations / merchant-automation controllers
exactly):

  * every handler returns ``(int status, dict body)``; ``body`` always has an ``ok`` bool;
  * the whole surface is DARK when ``BUSINESS_OS_CREATOR_COMMERCE`` is off — every
    handler returns 404;
  * informational only: nothing here moves money, pays out, or charges. Earnings are a
    reporting quantity; a tier is a label, not an entitlement grant;
  * only curated error codes are surfaced — never an internal exception string.
"""

from __future__ import annotations

import os
from typing import Any

from services.business_os.creator_commerce import schema as _schema
from services.business_os.creator_commerce import engine as _engine


FLAG_ENV = "BUSINESS_OS_CREATOR_COMMERCE"


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
def record_offering(payload: Any) -> tuple:
    """Declare a support offering (creator entry point)."""
    if not is_enabled():
        return _dark()
    if not isinstance(payload, dict):
        return _bad("missing_payload", "Expected a JSON body.")
    creator_id = payload.get("creator_id")
    offering_type = payload.get("offering_type")
    if creator_id is None or offering_type is None:
        return _bad("missing_fields",
                    "creator_id and offering_type are required.")
    ensure_ready()
    try:
        result = _engine.record_offering(
            creator_id, offering_type,
            name=payload.get("name"),
            unit_amount=payload.get("unit_amount"),
            currency=(payload.get("currency") or "USD"),
            active=(payload.get("active") if payload.get("active") is not None else True),
            source=(payload.get("source") or "manual"),
            external_ref=payload.get("external_ref"), meta=payload.get("meta"))
    except _engine.CreatorCommerceError as e:
        return _bad("invalid_offering", str(e))
    return (200, {"ok": True, "result": result})


def record_contribution(payload: Any) -> tuple:
    """Append a supporter contribution fact (feed/creator entry point). Records support
    that already happened — nothing is charged."""
    if not is_enabled():
        return _dark()
    if not isinstance(payload, dict):
        return _bad("missing_payload", "Expected a JSON body.")
    creator_id = payload.get("creator_id")
    supporter_id = payload.get("supporter_id")
    amount = payload.get("amount")
    if creator_id is None or supporter_id is None or amount is None:
        return _bad("missing_fields",
                    "creator_id, supporter_id and amount are required.")
    ensure_ready()
    try:
        result = _engine.record_contribution(
            creator_id, supporter_id, amount,
            offering_id=payload.get("offering_id"),
            currency=(payload.get("currency") or "USD"),
            occurred_at=payload.get("occurred_at"),
            source=(payload.get("source") or "manual"),
            external_ref=payload.get("external_ref"), meta=payload.get("meta"))
    except _engine.CreatorCommerceError as e:
        return _bad("invalid_contribution", str(e))
    return (200, {"ok": True, "result": result})


# ---------------------------------------------------------------------------
# computation + reporting
# ---------------------------------------------------------------------------
def supporters_report(creator_id: str, limit: int = 200) -> tuple:
    """The ranked supporter/tier projection for a creator. Computes on demand if the
    projection is empty so a first-time caller gets a result. Read-only."""
    if not is_enabled():
        return _dark()
    creator_id = str(creator_id or "").strip()
    if not creator_id:
        return _bad("missing_fields", "creator_id is required.")
    ensure_ready()
    from services import db
    conn = db.connect()
    try:
        rows = _engine.get_supporters(creator_id, limit=int(limit or 200), conn=conn)
        if not rows:
            _engine.compute_creator(creator_id, conn=conn)
            conn.commit()
            rows = _engine.get_supporters(creator_id, limit=int(limit or 200),
                                          conn=conn)
    finally:
        conn.close()
    return (200, {"ok": True, "result": {"creator_id": creator_id,
                                         "supporters": rows}})


def offerings_report(creator_id: str, limit: int = 200) -> tuple:
    """The declared offerings for a creator."""
    if not is_enabled():
        return _dark()
    creator_id = str(creator_id or "").strip()
    if not creator_id:
        return _bad("missing_fields", "creator_id is required.")
    ensure_ready()
    return (200, {"ok": True, "result": {"creator_id": creator_id,
                                         "offerings": _engine.list_offerings(
                                             creator_id, limit=int(limit or 200))}})


def earnings_report(creator_id: str) -> tuple:
    """Total support and a per-offering rollup for a creator. Informational only — no
    payout is implied."""
    if not is_enabled():
        return _dark()
    creator_id = str(creator_id or "").strip()
    if not creator_id:
        return _bad("missing_fields", "creator_id is required.")
    ensure_ready()
    return (200, {"ok": True, "result": _engine.earnings_report(creator_id)})


def run_recompute(creator_id: str) -> tuple:
    """Operator/cron entry point: recompute a creator's supporter/tier projection from
    the contribution log. Nothing is executed — a tier is a label, not a grant."""
    if not is_enabled():
        return _dark()
    creator_id = str(creator_id or "").strip()
    if not creator_id:
        return _bad("missing_fields", "creator_id is required.")
    ensure_ready()
    try:
        result = _engine.compute_creator(creator_id)
    except _engine.CreatorCommerceError as e:
        return _bad("invalid_request", str(e))
    return (200, {"ok": True, "result": result})
