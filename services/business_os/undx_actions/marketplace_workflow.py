"""UNDX governed Marketplace listing workflow.

This module is an adapter, not a second marketplace. It turns a bounded UNDX listing
brief into canonical Marketplace assistant calls, records the UNDX governance facts,
and stores receipts from verified Marketplace state.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Optional

from services.business_os.marketplace import assistant as _mkt_assistant
from services.business_os.marketplace import service as _mkt_service
from services.business_os.marketplace.service import MarketplaceError
from services.business_os.undx_actions import engine as _engine


ACTION_CREATE = "marketplace.product.create"
ACTION_PUBLISH = "marketplace.product.publish"
TOOL_CREATE = "marketplace.create_product"
TOOL_PUBLISH = "marketplace.publish_product"


def _payload_hash(payload: Any) -> str:
    raw = json.dumps(payload or {}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _require_dict(payload: Any) -> dict:
    if not isinstance(payload, dict):
        raise _engine.UndxActionsError("listing payload must be an object")
    return dict(payload)


def _clean_text(value: Any, max_len: int) -> str:
    return " ".join(str(value or "").strip().split())[:max_len]


def canonical_listing_params(payload: Any) -> dict:
    """Normalize UNDX listing input into canonical Marketplace create_product params.

    This accepts explicit structured fields. ``source_text`` may provide fallback title
    and description for native/UNDX drafts, but price and fulfillment remain explicit
    so UNDX cannot invent commercial terms silently.
    """
    p = _require_dict(payload)
    source_text = _clean_text(p.get("source_text"), 4000)
    title = _clean_text(p.get("title"), 160)
    if not title and source_text:
        title = _clean_text(source_text.splitlines()[0] if "\n" in source_text else source_text, 160)
    description = _clean_text(p.get("description"), 4000)
    if not description and source_text:
        description = source_text[:4000]
    try:
        price_cents = int(p.get("price_cents"))
    except (TypeError, ValueError):
        raise _engine.UndxActionsError("price_cents is required and must be an integer")
    fulfillment_type = str(p.get("fulfillment_type") or "physical").strip().lower()
    inventory_raw = p.get("inventory_qty")
    inventory_qty = None
    if inventory_raw not in (None, ""):
        try:
            inventory_qty = int(inventory_raw)
        except (TypeError, ValueError):
            raise _engine.UndxActionsError("inventory_qty must be an integer")
    currency = str(p.get("currency") or "usd").strip().lower() or "usd"
    params = {
        "title": title,
        "description": description,
        "price_cents": price_cents,
        "currency": currency,
        "fulfillment_type": fulfillment_type,
        "inventory_qty": inventory_qty,
    }
    # Reuse Marketplace validation without writing by validating through plan.
    if not params["title"]:
        raise _engine.UndxActionsError("title is required")
    if fulfillment_type not in _mkt_service.FULFILLMENT_TYPES:
        raise _engine.UndxActionsError("fulfillment_type must be physical or digital")
    if price_cents < 0 or price_cents > _mkt_service.PRICE_MAX_CENTS:
        raise _engine.UndxActionsError("price_cents out of range")
    if fulfillment_type == "physical" and (inventory_qty is None or inventory_qty <= 0):
        raise _engine.UndxActionsError("physical listings require positive inventory_qty")
    if inventory_qty is not None and inventory_qty < 0:
        raise _engine.UndxActionsError("inventory_qty cannot be negative")
    return params


def register_marketplace_tools(conn=None) -> list[dict]:
    """Register the canonical Marketplace tools UNDX may propose."""
    return [
        _engine.register_tool(
            TOOL_CREATE, ACTION_CREATE, product_area="marketplace", risk="low",
            confirmation_required=False,
            feature_flag=_mkt_service.FLAG_ENV,
            allowed_modes=["draft"], conn=conn),
        _engine.register_tool(
            TOOL_PUBLISH, ACTION_PUBLISH, product_area="marketplace", risk="high",
            confirmation_required=True,
            feature_flag=_mkt_service.FLAG_ENV,
            allowed_modes=["publish"], conn=conn),
    ]


def create_listing_draft(*, org_id: str, actor: str, user_id: Any,
                         listing: Any, source: str = "undx_marketplace",
                         external_ref: Optional[str] = None) -> dict:
    """Create a Marketplace draft through the governed UNDX path.

    If governance denies or requires approval, no Marketplace write runs. If allowed,
    the canonical Marketplace assistant creates a draft and this module records a
    verified receipt.
    """
    params = canonical_listing_params(listing)
    register_marketplace_tools()
    req = _engine.record_action_request(
        org_id, actor, ACTION_CREATE, risk="low", params=params,
        source=source, external_ref=external_ref,
        meta={"tool": TOOL_CREATE, "payload_hash": _payload_hash(params)})
    decision = _engine.evaluate_org(org_id)
    current = next((d for d in decision["decisions"]
                    if d["request_id"] == req["request_id"]), None)
    if current is None:
        raise _engine.UndxActionsError("governance decision missing")
    if current["effect"] != "allow":
        receipt = _engine.record_receipt(
            org_id, ACTION_CREATE, actor,
            "blocked" if current["effect"] == "deny" else "cancelled",
            request_id=req["request_id"],
            verification={"effect": current["effect"], "reason": current.get("reason")})
        return {"ok": False, "request": req, "decision": current,
                "receipt": receipt, "requires_approval": current["effect"] == "require_approval"}
    try:
        result = _mkt_assistant.execute(user_id, "create_product", params)
    except MarketplaceError as exc:
        receipt = _engine.record_receipt(
            org_id, ACTION_CREATE, actor, "failed", request_id=req["request_id"],
            verification={"code": exc.code}, result={"error": str(exc)})
        return {"ok": False, "request": req, "decision": current,
                "receipt": receipt, "error": str(exc), "code": exc.code}
    product_id = ((result.get("observed") or {}).get("product_id")
                  or (result.get("canonical_params") or {}).get("product_id"))
    receipt = _engine.record_receipt(
        org_id, ACTION_CREATE, actor,
        "verified" if result.get("verified") else "failed",
        request_id=req["request_id"],
        canonical_ref=f"marketplace_product:{product_id}" if product_id else None,
        verification=result.get("observed"), result=result)
    return {"ok": bool(result.get("verified")), "request": req, "decision": current,
            "receipt": receipt, "marketplace": result}


def plan_publish_listing(*, org_id: str, actor: str, user_id: Any,
                         product_id: Any, source: str = "undx_marketplace",
                         external_ref: Optional[str] = None) -> dict:
    params = {"product_id": _mkt_service._sid(product_id)}
    register_marketplace_tools()
    req = _engine.record_action_request(
        org_id, actor, ACTION_PUBLISH, risk="high", params=params,
        source=source, external_ref=external_ref,
        meta={"tool": TOOL_PUBLISH, "payload_hash": _payload_hash(params)})
    decision = _engine.evaluate_org(org_id)
    current = next((d for d in decision["decisions"]
                    if d["request_id"] == req["request_id"]), None)
    if current is None:
        raise _engine.UndxActionsError("governance decision missing")
    if current["effect"] == "deny":
        receipt = _engine.record_receipt(
            org_id, ACTION_PUBLISH, actor, "blocked", request_id=req["request_id"],
            canonical_ref=f"marketplace_product:{params['product_id']}",
            verification={"effect": "deny", "reason": current.get("reason")})
        return {"ok": False, "request": req, "decision": current, "receipt": receipt}
    plan = _mkt_assistant.plan(user_id, "publish_product", params)
    confirmation = _engine.record_confirmation(
        org_id, req["request_id"], actor, _payload_hash(params), status="pending",
        expires_at=plan.get("expires_at"),
        meta={"tool": TOOL_PUBLISH, "marketplace_confirmation_required": True})
    return {"ok": True, "request": req, "decision": current, "plan": plan,
            "confirmation": confirmation}


def execute_publish_listing(*, org_id: str, actor: str, user_id: Any,
                            request_id: str, product_id: Any,
                            confirmation_token: str) -> dict:
    params = {"product_id": _mkt_service._sid(product_id)}
    decision = _engine.evaluate_org(org_id)
    current = next((d for d in decision["decisions"]
                    if d["request_id"] == request_id), None)
    if (current is None
            or current.get("action_type") != ACTION_PUBLISH
            or current.get("actor") != actor):
        raise _engine.UndxActionsError(
            "publish request does not match this actor and action")
    if current.get("effect") == "deny":
        receipt = _engine.record_receipt(
            org_id, ACTION_PUBLISH, actor, "blocked", request_id=request_id,
            canonical_ref=f"marketplace_product:{params['product_id']}",
            verification={"effect": "deny", "reason": current.get("reason")})
        return {"ok": False, "decision": current, "receipt": receipt,
                "error": "Publish is blocked by current governance.",
                "code": "governance_denied"}
    confirmation = _engine.redeem_confirmation(
        org_id, request_id, actor, _payload_hash(params))
    try:
        result = _mkt_assistant.execute(
            user_id, "publish_product", params,
            confirmation_token=confirmation_token)
    except MarketplaceError as exc:
        receipt = _engine.record_receipt(
            org_id, ACTION_PUBLISH, actor, "failed", request_id=request_id,
            canonical_ref=f"marketplace_product:{params['product_id']}",
            verification={"code": exc.code}, result={"error": str(exc)})
        return {"ok": False, "receipt": receipt, "error": str(exc), "code": exc.code}
    receipt = _engine.record_receipt(
        org_id, ACTION_PUBLISH, actor,
        "verified" if result.get("verified") else "failed",
        request_id=request_id,
        canonical_ref=f"marketplace_product:{params['product_id']}",
        verification=result.get("observed"), result=result)
    return {"ok": bool(result.get("verified")), "confirmation": confirmation,
            "decision": current, "receipt": receipt,
            "marketplace": result}
