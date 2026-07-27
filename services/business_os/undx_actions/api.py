"""Business OS — Governed UNDX actions: framework-agnostic controller (Stage 6).

bot.py owns the raw request, auth (session/token -> user_id) and CSRF; it turns the
returned ``(status, body)`` tuple into a Flask response. All decision logic lives here
so it is unit-testable without Flask.

Contract (mirrors the attribution / recommendations / merchant-automation /
creator-commerce controllers exactly):

  * every handler returns ``(int status, dict body)``; ``body`` always has an ``ok`` bool;
  * the whole surface is DARK when ``BUSINESS_OS_UNDX_ACTIONS`` is off — every handler
    returns 404;
  * informational only: nothing here executes an action. A decision is a governance
    label;
  * only curated error codes are surfaced — never an internal exception string.
"""

from __future__ import annotations

import os
from typing import Any, Optional

from services.business_os.undx_actions import schema as _schema
from services.business_os.undx_actions import engine as _engine
from services.business_os.undx_actions import marketplace_workflow as _marketplace


FLAG_ENV = "BUSINESS_OS_UNDX_ACTIONS"


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
def record_policy(payload: Any) -> tuple:
    """Declare a governance policy (operator/org entry point)."""
    if not is_enabled():
        return _dark()
    if not isinstance(payload, dict):
        return _bad("missing_payload", "Expected a JSON body.")
    org_id = payload.get("org_id")
    action_type = payload.get("action_type")
    effect = payload.get("effect")
    if org_id is None or action_type is None or effect is None:
        return _bad("missing_fields",
                    "org_id, action_type and effect are required.")
    ensure_ready()
    try:
        result = _engine.record_policy(
            org_id, action_type, effect,
            name=payload.get("name"),
            max_risk=payload.get("max_risk"),
            active=(payload.get("active") if payload.get("active") is not None else True),
            priority=(payload.get("priority") if payload.get("priority") is not None else 0),
            source=(payload.get("source") or "manual"),
            external_ref=payload.get("external_ref"), meta=payload.get("meta"))
    except _engine.UndxActionsError as e:
        return _bad("invalid_policy", str(e))
    return (200, {"ok": True, "result": result})


def record_action_request(payload: Any) -> tuple:
    """Append a proposed action-request fact (feed/agent entry point). Records a
    proposal — nothing is executed."""
    if not is_enabled():
        return _dark()
    if not isinstance(payload, dict):
        return _bad("missing_payload", "Expected a JSON body.")
    org_id = payload.get("org_id")
    actor = payload.get("actor")
    action_type = payload.get("action_type")
    if org_id is None or actor is None or action_type is None:
        return _bad("missing_fields",
                    "org_id, actor and action_type are required.")
    ensure_ready()
    try:
        result = _engine.record_action_request(
            org_id, actor, action_type,
            subject_ref=payload.get("subject_ref"),
            risk=(payload.get("risk") if payload.get("risk") is not None else "low"),
            params=payload.get("params"),
            requested_at=payload.get("requested_at"),
            source=(payload.get("source") or "manual"),
            external_ref=payload.get("external_ref"), meta=payload.get("meta"))
    except _engine.UndxActionsError as e:
        return _bad("invalid_request", str(e))
    return (200, {"ok": True, "result": result})


def register_tool(payload: Any) -> tuple:
    """Register a canonical UNDX tool descriptor. Does not execute the tool."""
    if not is_enabled():
        return _dark()
    if not isinstance(payload, dict):
        return _bad("missing_payload", "Expected a JSON body.")
    if not payload.get("tool_name") or not payload.get("action_type"):
        return _bad("missing_fields", "tool_name and action_type are required.")
    ensure_ready()
    try:
        result = _engine.register_tool(
            payload.get("tool_name"), payload.get("action_type"),
            version=(payload.get("version") or "v1"),
            product_area=payload.get("product_area"),
            risk=(payload.get("risk") if payload.get("risk") is not None else "low"),
            confirmation_required=payload.get("confirmation_required"),
            feature_flag=payload.get("feature_flag"),
            enabled=(payload.get("enabled") if payload.get("enabled") is not None else True),
            allowed_modes=payload.get("allowed_modes"),
            meta=payload.get("meta"))
    except _engine.UndxActionsError as e:
        return _bad("invalid_tool", str(e))
    return (200, {"ok": True, "result": result})


def grant_permission(payload: Any) -> tuple:
    """Append an actor-scoped permission fact."""
    if not is_enabled():
        return _dark()
    if not isinstance(payload, dict):
        return _bad("missing_payload", "Expected a JSON body.")
    if not payload.get("org_id") or not payload.get("actor") or not payload.get("action_type"):
        return _bad("missing_fields", "org_id, actor and action_type are required.")
    ensure_ready()
    try:
        result = _engine.grant_permission(
            payload.get("org_id"), payload.get("actor"), payload.get("action_type"),
            (payload.get("effect") or "allow"),
            scope_ref=payload.get("scope_ref"),
            max_risk=payload.get("max_risk"),
            active=(payload.get("active") if payload.get("active") is not None else True),
            priority=(payload.get("priority") if payload.get("priority") is not None else 0),
            source=(payload.get("source") or "manual"),
            external_ref=payload.get("external_ref"),
            expires_at=payload.get("expires_at"),
            meta=payload.get("meta"))
    except _engine.UndxActionsError as e:
        return _bad("invalid_permission", str(e))
    return (200, {"ok": True, "result": result})


def record_confirmation(payload: Any) -> tuple:
    if not is_enabled():
        return _dark()
    if not isinstance(payload, dict):
        return _bad("missing_payload", "Expected a JSON body.")
    required = ("org_id", "request_id", "actor", "payload_hash")
    if any(not payload.get(k) for k in required):
        return _bad("missing_fields", "org_id, request_id, actor and payload_hash are required.")
    ensure_ready()
    try:
        result = _engine.record_confirmation(
            payload.get("org_id"), payload.get("request_id"), payload.get("actor"),
            payload.get("payload_hash"),
            status=(payload.get("status") or "pending"),
            expires_at=payload.get("expires_at"),
            confirmed_at=payload.get("confirmed_at"),
            meta=payload.get("meta"))
    except _engine.UndxActionsError as e:
        return _bad("invalid_confirmation", str(e))
    return (200, {"ok": True, "result": result})


def record_receipt(payload: Any) -> tuple:
    if not is_enabled():
        return _dark()
    if not isinstance(payload, dict):
        return _bad("missing_payload", "Expected a JSON body.")
    required = ("org_id", "action_type", "actor", "status")
    if any(not payload.get(k) for k in required):
        return _bad("missing_fields", "org_id, action_type, actor and status are required.")
    ensure_ready()
    try:
        result = _engine.record_receipt(
            payload.get("org_id"), payload.get("action_type"), payload.get("actor"),
            payload.get("status"),
            request_id=payload.get("request_id"),
            canonical_ref=payload.get("canonical_ref"),
            verification=payload.get("verification"),
            result=payload.get("result"))
    except _engine.UndxActionsError as e:
        return _bad("invalid_receipt", str(e))
    return (200, {"ok": True, "result": result})


def activate_emergency_stop(payload: Any) -> tuple:
    if not is_enabled():
        return _dark()
    if not isinstance(payload, dict):
        return _bad("missing_payload", "Expected a JSON body.")
    if not payload.get("org_id") or not payload.get("actor") or not payload.get("reason"):
        return _bad("missing_fields", "org_id, actor and reason are required.")
    ensure_ready()
    try:
        result = _engine.activate_emergency_stop(
            payload.get("org_id"), payload.get("actor"), payload.get("reason"),
            action_type=(payload.get("action_type") or "*"),
            active=(payload.get("active") if payload.get("active") is not None else True),
            meta=payload.get("meta"))
    except _engine.UndxActionsError as e:
        return _bad("invalid_stop", str(e))
    return (200, {"ok": True, "result": result})


# ---------------------------------------------------------------------------
# evaluation + reporting
# ---------------------------------------------------------------------------
def decisions_report(org_id: str, limit: int = 200) -> tuple:
    """The governance decisions for an org. Computes on demand if the projection is
    empty so a first-time caller gets a result. Read-only; nothing is executed."""
    if not is_enabled():
        return _dark()
    org_id = str(org_id or "").strip()
    if not org_id:
        return _bad("missing_fields", "org_id is required.")
    ensure_ready()
    from services import db
    conn = db.connect()
    try:
        rows = _engine.get_decisions(org_id, limit=int(limit or 200), conn=conn)
        if not rows:
            _engine.evaluate_org(org_id, conn=conn)
            conn.commit()
            rows = _engine.get_decisions(org_id, limit=int(limit or 200), conn=conn)
    finally:
        conn.close()
    return (200, {"ok": True, "result": {"org_id": org_id, "decisions": rows}})


def policies_report(org_id: str, limit: int = 200) -> tuple:
    """The declared policies for an org."""
    if not is_enabled():
        return _dark()
    org_id = str(org_id or "").strip()
    if not org_id:
        return _bad("missing_fields", "org_id is required.")
    ensure_ready()
    return (200, {"ok": True, "result": {"org_id": org_id,
                                         "policies": _engine.list_policies(
                                             org_id, limit=int(limit or 200))}})


def requests_report(org_id: str, limit: int = 500) -> tuple:
    """The proposed action requests for an org."""
    if not is_enabled():
        return _dark()
    org_id = str(org_id or "").strip()
    if not org_id:
        return _bad("missing_fields", "org_id is required.")
    ensure_ready()
    return (200, {"ok": True, "result": {"org_id": org_id,
                                         "requests": _engine.list_requests(
                                             org_id, limit=int(limit or 500))}})


def tools_report(product_area: str = "", limit: int = 200) -> tuple:
    if not is_enabled():
        return _dark()
    ensure_ready()
    return (200, {"ok": True, "result": {"tools": _engine.list_tools(
        product_area=(product_area or None), limit=int(limit or 200))}})


def permissions_report(org_id: str, actor: str = "", limit: int = 200) -> tuple:
    if not is_enabled():
        return _dark()
    org_id = str(org_id or "").strip()
    if not org_id:
        return _bad("missing_fields", "org_id is required.")
    ensure_ready()
    return (200, {"ok": True, "result": {"org_id": org_id,
                                         "permissions": _engine.list_permissions(
                                             org_id, actor=(actor or None),
                                             limit=int(limit or 200))}})


def action_center_report(org_id: str, limit: int = 100,
                         actor: str = "") -> tuple:
    if not is_enabled():
        return _dark()
    org_id = str(org_id or "").strip()
    if not org_id:
        return _bad("missing_fields", "org_id is required.")
    ensure_ready()
    try:
        result = _engine.action_center(
            org_id, actor=(actor or None), limit=int(limit or 100))
    except _engine.UndxActionsError as e:
        return _bad("invalid_request", str(e))
    return (200, {"ok": True, "result": result})


def marketplace_create_listing_draft(user_id: Any, payload: Any, *,
                                     trusted_org_id: Optional[str] = None,
                                     trusted_actor: Optional[str] = None) -> tuple:
    """Create a Marketplace listing draft through UNDX governance.

    Required payload fields: org_id, actor, listing. The listing is normalized into
    canonical Marketplace create_product params.
    """
    if not is_enabled():
        return _dark()
    if not isinstance(payload, dict):
        return _bad("missing_payload", "Expected a JSON body.")
    org_id = trusted_org_id or payload.get("org_id")
    actor = trusted_actor or payload.get("actor")
    if not org_id or not actor or not isinstance(payload.get("listing"), dict):
        return _bad("missing_fields", "org_id, actor and listing are required.")
    ensure_ready()
    try:
        result = _marketplace.create_listing_draft(
            org_id=org_id,
            actor=actor,
            user_id=user_id,
            listing=payload.get("listing"),
            source=(payload.get("source") or "undx_marketplace"),
            external_ref=payload.get("external_ref"))
    except _engine.UndxActionsError as e:
        return _bad("invalid_listing", str(e))
    return (200 if result.get("ok") else 409, {"ok": bool(result.get("ok")), "result": result})


def marketplace_plan_publish(user_id: Any, payload: Any, *,
                             trusted_org_id: Optional[str] = None,
                             trusted_actor: Optional[str] = None) -> tuple:
    if not is_enabled():
        return _dark()
    if not isinstance(payload, dict):
        return _bad("missing_payload", "Expected a JSON body.")
    org_id = trusted_org_id or payload.get("org_id")
    actor = trusted_actor or payload.get("actor")
    if not org_id or not actor or not payload.get("product_id"):
        return _bad("missing_fields", "org_id, actor and product_id are required.")
    ensure_ready()
    try:
        result = _marketplace.plan_publish_listing(
            org_id=org_id,
            actor=actor,
            user_id=user_id,
            product_id=payload.get("product_id"),
            source=(payload.get("source") or "undx_marketplace"),
            external_ref=payload.get("external_ref"))
    except _engine.UndxActionsError as e:
        return _bad("invalid_publish_plan", str(e))
    return (200 if result.get("ok") else 409, {"ok": bool(result.get("ok")), "result": result})


def marketplace_execute_publish(user_id: Any, payload: Any, *,
                                trusted_org_id: Optional[str] = None,
                                trusted_actor: Optional[str] = None) -> tuple:
    if not is_enabled():
        return _dark()
    if not isinstance(payload, dict):
        return _bad("missing_payload", "Expected a JSON body.")
    org_id = trusted_org_id or payload.get("org_id")
    actor = trusted_actor or payload.get("actor")
    required = ("request_id", "product_id", "confirmation_token")
    if not org_id or not actor or any(not payload.get(k) for k in required):
        return _bad("missing_fields",
                    "org_id, actor, request_id, product_id and confirmation_token are required.")
    ensure_ready()
    try:
        result = _marketplace.execute_publish_listing(
            org_id=org_id,
            actor=actor,
            user_id=user_id,
            request_id=payload.get("request_id"),
            product_id=payload.get("product_id"),
            confirmation_token=payload.get("confirmation_token"))
    except _engine.UndxActionsError as e:
        return _bad("invalid_publish_execute", str(e))
    return (200 if result.get("ok") else 409, {"ok": bool(result.get("ok")), "result": result})


def run_evaluate(org_id: str) -> tuple:
    """Operator/cron entry point: re-evaluate an org's action requests against active
    policies and rebuild the decision projection. Nothing is executed — decisions are
    governance labels."""
    if not is_enabled():
        return _dark()
    org_id = str(org_id or "").strip()
    if not org_id:
        return _bad("missing_fields", "org_id is required.")
    ensure_ready()
    try:
        result = _engine.evaluate_org(org_id)
    except _engine.UndxActionsError as e:
        return _bad("invalid_request", str(e))
    return (200, {"ok": True, "result": result})
