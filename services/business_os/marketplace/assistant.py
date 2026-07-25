"""Business OS — Marketplace: the governed UNDX Marketplace Assistant.

A thin, SERVER-AUTHORITATIVE governor over the canonical marketplace verbs — the exact
same governance the advertising assistant enforces, applied to commerce:

  1. **Confirmation before any consequential change.** Paying, fulfilling, completing,
     and cancelling an order (and publishing/pausing a product) are two-phase: ``plan``
     returns a summary + a ``confirmation_token`` bound to the EXACT tool and canonical
     params; ``execute`` refuses to run a confirmation-gated tool unless the caller echoes
     back the matching token. A token minted for one action can never execute a different
     one. The approval is a server-side GRANT — single-use, time-limited (default 300s),
     revocable, bound to one actor/tool/payload, and stored only as a sha256 — so an
     approval cannot be replayed to repeat a consequential action. Reads run immediately.

  2. **Every claimed action is verified against canonical backend state.** The assistant
     NEVER reports success from a verb's return value. After a write it RE-READS the
     authoritative order/product and asserts the observed state matches the intent
     (e.g. ``status == 'paid'`` after pay). ``verified`` is only True when canonical state
     confirms it.

This assistant owns no subsystem-specific tables; confirmation grants live in the
shared Business OS confirmation service. Every money movement it can trigger goes
through the ``orders`` verbs, which post to the shared ledger. A dedicated kill switch
(``BUSINESS_OS_MARKETPLACE_ASSISTANT_DISABLE_WRITES``) disables the write tools without
touching reads.
"""

from __future__ import annotations

import os
from typing import Any, Optional

from services.business_os import confirmations as _cf
from services.business_os import results as _res
from services.business_os.marketplace import service as _svc
from services.business_os.marketplace import orders as _ord
from services.business_os.marketplace import refunds as _rf
from services.business_os.marketplace.service import MarketplaceError


DISABLE_WRITES_ENV = "BUSINESS_OS_MARKETPLACE_ASSISTANT_DISABLE_WRITES"

# Approvals live in the shared Business OS confirmation-grant service, namespaced so a
# marketplace approval can never be redeemed against advertising (or anything else).
CONFIRM_NAMESPACE = "marketplace"
_CONFIRM_TABLE = _cf.TABLE
_token_hash = _cf.token_hash

# Kept for backward compatibility: callers and tests that referenced the marketplace's
# own TTL knob keep working, but the shared service owns the clamping.
_TTL_ENV = "BUSINESS_OS_MARKETPLACE_ASSISTANT_CONFIRM_TTL_SECONDS"


def _writes_disabled() -> bool:
    return str(os.environ.get(DISABLE_WRITES_ENV) or "").strip().lower() in {"1", "true", "on", "yes"}


def _ttl_override() -> Optional[int]:
    """Subsystem-specific TTL override, if the operator pinned one."""
    raw = os.environ.get(_TTL_ENV)
    if raw in (None, ""):
        return None
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        return None


# --- canonical parameter normalization --------------------------------------
def _norm_params(tool: str, params: dict) -> dict:
    """Reduce raw params to the CANONICAL fields a tool consumes. The token binds to
    this reduced set, so extra client noise cannot change identity."""
    p = params or {}
    out: dict = {}
    if tool in {"pay_order", "fulfill_order", "complete_order", "cancel_order", "order_status"}:
        out["order_id"] = _svc._sid(p.get("order_id")) if p.get("order_id") is not None else None
        if tool == "fulfill_order":
            out["tracking_ref"] = p.get("tracking_ref")
    elif tool == "create_order":
        out["product_id"] = _svc._sid(p.get("product_id")) if p.get("product_id") is not None else None
        out["quantity"] = int(p.get("quantity") or 1)
    elif tool in {"publish_product", "pause_product"}:
        out["product_id"] = _svc._sid(p.get("product_id")) if p.get("product_id") is not None else None
    elif tool == "create_product":
        out["title"] = p.get("title")
        out["description"] = p.get("description")
        out["price_cents"] = int(p.get("price_cents")) if p.get("price_cents") is not None else None
        out["currency"] = str(p.get("currency") or "usd").lower()
        out["fulfillment_type"] = p.get("fulfillment_type") or "physical"
        out["inventory_qty"] = (int(p.get("inventory_qty"))
                                if p.get("inventory_qty") is not None else None)
    elif tool in {"payout_balance", "product_reviews", "seller_products"}:
        if p.get("product_id") is not None:
            out["product_id"] = _svc._sid(p.get("product_id"))
    return out


# --- confirmation grants (delegated to the shared service) -------------------
# An approval is a ROW in ``services.business_os.confirmations``, not a derivable hash.
# A derived hash is reproducible from (salt, user, tool, params), so it stays valid
# forever and is reusable without limit — approving "publish product X" once would
# approve every future publish of X. The shared grant is bound to one actor, one tool
# and one canonical payload, expires, is revocable, and is consumed exactly once.

def _mint_confirmation(user_id: Any, tool: str, canonical: dict) -> tuple:
    """Mint a marketplace-namespaced grant. Returns (raw_token, grant_metadata)."""
    grant = _cf.mint(CONFIRM_NAMESPACE, _svc._sid(user_id), tool, canonical,
                     ttl_override=_ttl_override())
    return grant["confirmation_token"], grant


def _consume_confirmation(user_id: Any, tool: str, canonical: dict, raw: str) -> None:
    """Redeem the approval or raise a MarketplaceError carrying the shared code.

    Codes are re-raised unchanged so the governance contract reads identically through
    every subsystem's API: 428 ``confirmation_required``, 409 ``confirmation_mismatch``
    / ``confirmation_expired`` / ``confirmation_used`` / ``confirmation_revoked``.
    """
    try:
        _cf.consume(CONFIRM_NAMESPACE, _svc._sid(user_id), tool, canonical, raw)
    except _cf.ConfirmationError as exc:
        raise MarketplaceError(str(exc), exc.http_status, exc.code) from exc


def revoke_confirmation(user_id: Any, confirmation_token: str) -> dict:
    """Withdraw an approval before it is redeemed. Only its own actor may revoke it."""
    _svc._require_enabled()
    return _cf.revoke(CONFIRM_NAMESPACE, _svc._sid(user_id), confirmation_token)


# --- write handlers + verifiers ---------------------------------------------
def _h_create_product(uid, c):
    return _svc.create_product(uid, title=c["title"], price_cents=c["price_cents"],
                               description=c.get("description"),
                               currency=c.get("currency", "usd"),
                               fulfillment_type=c.get("fulfillment_type", "physical"),
                               inventory_qty=c.get("inventory_qty"))


def _v_create_product(uid, c, result):
    pid = (result or {}).get("product_id")
    if pid is None:
        return False, {"product_id": None}
    cur = _svc.get_product(pid, requester_user_id=uid)
    return (cur or {}).get("status") == "draft", {"product_id": pid, "status": (cur or {}).get("status")}


def _h_create_order(uid, c):
    return _ord.create_order(uid, c["product_id"], quantity=c.get("quantity", 1))


def _v_create_order(uid, c, result):
    oid = (result or {}).get("order_id")
    if oid is None:
        return False, {"order_id": None}
    cur = _ord.get_order(oid, requester_user_id=uid)
    return (cur or {}).get("status") == "created", {"order_id": oid, "status": (cur or {}).get("status")}


def _h_pay(uid, c):
    _ord.pay_order(c["order_id"], uid)


def _h_fulfill(uid, c):
    _ord.fulfill_order(c["order_id"], uid, tracking_ref=c.get("tracking_ref"))


def _h_complete(uid, c):
    _ord.complete_order(c["order_id"], uid)


def _h_cancel(uid, c):
    _ord.cancel_order(c["order_id"], uid)


def _h_publish(uid, c):
    _svc.transition_product(uid, c["product_id"], "publish")


def _h_pause(uid, c):
    _svc.transition_product(uid, c["product_id"], "pause")


def _v_order_status(expected):
    def _verify(uid, c):
        cur = _ord.get_order(c["order_id"], requester_user_id=uid)
        obs = (cur or {}).get("status")
        return obs == expected, {"status": obs}
    return _verify


def _v_product_status(expected):
    def _verify(uid, c):
        cur = _svc.get_product(c["product_id"], requester_user_id=uid)
        obs = (cur or {}).get("status")
        return obs == expected, {"status": obs}
    return _verify


# --- read tools -------------------------------------------------------------
def _r_order_status(uid, p):
    cur = _ord.get_order(_svc._sid(p.get("order_id")), requester_user_id=uid)
    if cur is None:
        raise MarketplaceError("Order not found.", 404, "not_found")
    return {"order_id": cur.get("order_id"), "status": cur.get("status"),
            "total_cents": cur.get("total_cents"), "refunded_cents": cur.get("refunded_cents")}


def _r_seller_products(uid, p):
    return _svc.list_products(uid, limit=100)


def _r_payout_balance(uid, p):
    return _rf.seller_payout_balance(uid)


def _r_product_reviews(uid, p):
    pid = _svc._sid(p.get("product_id"))
    return {"summary": _rf.product_rating_summary(pid), "reviews": _rf.list_reviews(pid)}


# --- registry ---------------------------------------------------------------
_TOOLS: dict = {
    "order_status": {"confirm": False, "write": False, "read": _r_order_status,
                     "summary": "Read the status + money summary of one of your orders."},
    "seller_products": {"confirm": False, "write": False, "read": _r_seller_products,
                        "summary": "List your own product catalog."},
    "payout_balance": {"confirm": False, "write": False, "read": _r_payout_balance,
                       "summary": "Read your accrued seller payable balance."},
    "product_reviews": {"confirm": False, "write": False, "read": _r_product_reviews,
                        "summary": "Read the reviews + rating summary for a product."},
    # low-risk writes (reversible, spend nothing) — no confirmation, still verified.
    "create_product": {"confirm": False, "write": True, "handler": _h_create_product,
                       "verify_result": _v_create_product,
                       "summary": "Create a DRAFT product (nothing is listed or sold yet)."},
    "create_order": {"confirm": False, "write": True, "handler": _h_create_order,
                     "verify_result": _v_create_order,
                     "summary": "Create an order in CREATED state (no money moves until you pay)."},
    # consequential writes — confirmation + read-after-write verification.
    "pay_order": {"confirm": True, "write": True, "handler": _h_pay,
                  "verify": _v_order_status("paid"), "risk": "high",
                  "summary": "Pay an order: captures the total into escrow and decrements inventory."},
    "fulfill_order": {"confirm": True, "write": True, "handler": _h_fulfill,
                      "verify": _v_order_status("fulfilled"), "risk": "high",
                      "summary": "Mark an order fulfilled/shipped."},
    "complete_order": {"confirm": True, "write": True, "handler": _h_complete,
                       "verify": _v_order_status("completed"), "risk": "high",
                       "summary": "Complete an order: settles escrow to platform fee + seller payable."},
    "cancel_order": {"confirm": True, "write": True, "handler": _h_cancel,
                     "verify": _v_order_status("cancelled"), "risk": "high",
                     "summary": "Cancel an unpaid order."},
    "publish_product": {"confirm": True, "write": True, "handler": _h_publish,
                        "verify": _v_product_status("active"), "risk": "high",
                        "summary": "Publish a product so buyers can order it."},
    "pause_product": {"confirm": True, "write": True, "handler": _h_pause,
                      "verify": _v_product_status("paused"), "risk": "high",
                      "summary": "Pause a product so it stops being orderable."},
}


def list_tools() -> list:
    out = []
    for name, spec in _TOOLS.items():
        out.append({
            "tool": name,
            "requires_confirmation": bool(spec.get("confirm")),
            "is_write": bool(spec.get("write")),
            "risk": spec.get("risk", "read_only" if not spec.get("write") else "low"),
            "summary": spec.get("summary"),
        })
    return out


def _spec(tool: str) -> dict:
    spec = _TOOLS.get(tool)
    if spec is None:
        raise MarketplaceError(f"Unknown assistant tool: {tool!r}.", 400, "unknown_tool")
    return spec


def _requires_id(tool, canonical):
    if tool in {"pay_order", "fulfill_order", "complete_order", "cancel_order"} \
            and not canonical.get("order_id"):
        raise MarketplaceError("order_id is required.", 400, "order_id_required")
    if tool in {"publish_product", "pause_product"} and not canonical.get("product_id"):
        raise MarketplaceError("product_id is required.", 400, "product_id_required")


# --- public API -------------------------------------------------------------
def plan(user_id: Any, tool: str, params: Optional[dict] = None) -> dict:
    """Phase 1. For a confirmation-gated tool: validate shape + mint a token bound to
    (user, tool, canonical params). For a read-only tool: run it now. Never mutates."""
    _svc._require_enabled()
    spec = _spec(tool)
    params = params or {}
    if not spec.get("confirm"):
        if spec.get("write"):
            canonical = _norm_params(tool, params)
            return {"tool": tool, "requires_confirmation": False, "write": True,
                    "canonical_params": canonical, "summary": spec.get("summary")}
        result = spec["read"](user_id, params)
        return {"tool": tool, "requires_confirmation": False, "write": False, "result": result}
    canonical = _norm_params(tool, params)
    _requires_id(tool, canonical)
    token, grant = _mint_confirmation(user_id, tool, canonical)
    return {
        "tool": tool, "requires_confirmation": True, "write": True,
        "risk": spec.get("risk", "high"), "canonical_params": canonical,
        "summary": spec.get("summary"), "confirmation_token": token,
        "expires_at": grant["expires_at"], "ttl_seconds": grant["ttl_seconds"],
        "single_use": True,
    }


def execute(user_id: Any, tool: str, params: Optional[dict] = None, *,
            confirmation_token: Optional[str] = None) -> dict:
    """Phase 2. A confirmation-gated tool REQUIRES a token matching a freshly-computed
    token for these exact canonical params (428 without, 409 on mismatch). After a
    write the canonical state is RE-READ and ``verified`` reflects observed truth.

    ``ok`` is derived from that verification on write paths — see
    ``services.business_os.results``. A caller that needs "the verb ran" rather than "the
    verb is confirmed" reads ``write_applied``."""
    _svc._require_enabled()
    spec = _spec(tool)
    params = params or {}

    if not spec.get("write"):
        return _res.read_result(tool, spec["read"](user_id, params))

    if _writes_disabled():
        raise MarketplaceError("Marketplace assistant writes are disabled.", 409, "writes_disabled")

    canonical = _norm_params(tool, params)

    if spec.get("confirm"):
        _requires_id(tool, canonical)
        if not confirmation_token:
            raise MarketplaceError(
                "This action requires confirmation. Call plan() and confirm the token.",
                428, "confirmation_required")
        # Redeemed BEFORE the handler runs: a burnt approval must not be replayable
        # even if the underlying verb then fails.
        _consume_confirmation(user_id, tool, canonical, confirmation_token)

    result = spec["handler"](user_id, canonical)

    if "verify_result" in spec:
        ok, observed = spec["verify_result"](user_id, canonical, result)
    else:
        ok, observed = spec["verify"](user_id, canonical)

    return _res.write_result(tool, ok, observed, canonical)
