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

This module owns NO tables, and every money movement it can trigger goes through the
``orders`` verbs, which post to the shared ledger. A dedicated kill switch
(``BUSINESS_OS_MARKETPLACE_ASSISTANT_DISABLE_WRITES``) disables the write tools without
touching reads.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from services import db as _db
from services.business_os.marketplace import service as _svc
from services.business_os.marketplace import orders as _ord
from services.business_os.marketplace import refunds as _rf
from services.business_os.marketplace.service import MarketplaceError


DISABLE_WRITES_ENV = "BUSINESS_OS_MARKETPLACE_ASSISTANT_DISABLE_WRITES"
_TTL_ENV = "BUSINESS_OS_MARKETPLACE_ASSISTANT_CONFIRM_TTL_SECONDS"

_CONFIRM_TABLE = "business_os_mkt_assistant_confirmations"
_TTL_DEFAULT = 300
_TTL_MIN = 30
_TTL_MAX = 900


def _writes_disabled() -> bool:
    return str(os.environ.get(DISABLE_WRITES_ENV) or "").strip().lower() in {"1", "true", "on", "yes"}


def _ttl_seconds() -> int:
    raw = os.environ.get(_TTL_ENV)
    try:
        val = int(str(raw).strip()) if raw not in (None, "") else _TTL_DEFAULT
    except (TypeError, ValueError):
        val = _TTL_DEFAULT
    return max(_TTL_MIN, min(val, _TTL_MAX))


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    """Fixed-width UTC stamp. Same shape service.py uses, so it sorts lexicographically."""
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


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


# --- confirmation grants ----------------------------------------------------
# An approval is a ROW, not a derivable hash. A derived hash is reproducible from
# (salt, user, tool, params), so it is valid forever and reusable without limit —
# an operator who approves "publish product X" once has, in effect, approved every
# future publish of X. A stored grant is bound to one actor, one tool, one canonical
# payload, expires, and is consumed exactly once. The raw token is never persisted;
# only its sha256 is, so a database read cannot yield a usable approval.

def _params_hash(tool: str, canonical: dict) -> str:
    payload = json.dumps({"t": tool, "p": canonical}, sort_keys=True,
                         separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _token_hash(raw: str) -> str:
    return hashlib.sha256(str(raw).encode("utf-8")).hexdigest()


def _ensure_confirm_table(conn) -> None:
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_CONFIRM_TABLE} (
            token_hash TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            tool TEXT NOT NULL,
            params_hash TEXT NOT NULL,
            params_json TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            expires_at TEXT NOT NULL,
            created_at TEXT NOT NULL,
            consumed_at TEXT
        )
        """
    )


def _mint_confirmation(user_id: Any, tool: str, canonical: dict) -> tuple:
    """Create a single-use, time-limited approval and return (raw_token, expires_at)."""
    raw = secrets.token_urlsafe(32)
    now = _now()
    expires = now + timedelta(seconds=_ttl_seconds())
    conn = _db.connect()
    try:
        _ensure_confirm_table(conn)
        conn.execute(
            f"INSERT INTO {_CONFIRM_TABLE} (token_hash, user_id, tool, params_hash, "
            "params_json, status, expires_at, created_at) "
            "VALUES (?, ?, ?, ?, ?, 'pending', ?, ?)",
            (_token_hash(raw), _svc._sid(user_id), tool,
             _params_hash(tool, canonical),
             json.dumps(canonical, sort_keys=True, default=str),
             _iso(expires), _iso(now)))
        conn.commit()
    finally:
        conn.close()
    return raw, _iso(expires)


def _consume_confirmation(user_id: Any, tool: str, canonical: dict, raw: str) -> None:
    """Atomically redeem an approval, or raise. Never reveals why a token is unknown.

    Distinguishes the failure modes the caller can legitimately act on:
      * unknown / forged / wrong actor / wrong tool / edited payload -> 409 confirmation_mismatch
      * expired                                                     -> 409 confirmation_expired
      * already redeemed (replay)                                   -> 409 confirmation_used
      * explicitly revoked                                          -> 409 confirmation_revoked
    """
    th = _token_hash(raw)
    conn = _db.connect()
    try:
        _ensure_confirm_table(conn)
        row = _svc._row(conn.execute(
            f"SELECT * FROM {_CONFIRM_TABLE} WHERE token_hash = ?", (th,)).fetchone())
        if row is None:
            raise MarketplaceError(
                "Confirmation token does not match this exact action.",
                409, "confirmation_mismatch")

        # Binding checks BEFORE status/expiry so a mis-bound token never reports
        # "expired"/"used" — that would confirm the token exists to a guesser.
        if (str(row.get("user_id")) != _svc._sid(user_id)
                or str(row.get("tool")) != tool
                or str(row.get("params_hash")) != _params_hash(tool, canonical)):
            raise MarketplaceError(
                "Confirmation token does not match this exact action.",
                409, "confirmation_mismatch")

        status = str(row.get("status") or "")
        if status == "revoked":
            raise MarketplaceError("This confirmation was revoked.",
                                   409, "confirmation_revoked")
        if status != "pending":
            raise MarketplaceError(
                "This confirmation was already used. Confirm the action again.",
                409, "confirmation_used")
        if str(row.get("expires_at") or "") <= _iso(_now()):
            raise MarketplaceError(
                "This confirmation expired. Confirm the action again.",
                409, "confirmation_expired")

        # Single-use: only the caller whose UPDATE actually flips 'pending' proceeds.
        cur = conn.execute(
            f"UPDATE {_CONFIRM_TABLE} SET status = 'consumed', consumed_at = ? "
            "WHERE token_hash = ? AND status = 'pending'", (_iso(_now()), th))
        if int(getattr(cur, "rowcount", 0) or 0) != 1:
            conn.rollback()
            raise MarketplaceError(
                "This confirmation was already used. Confirm the action again.",
                409, "confirmation_used")
        conn.commit()
    finally:
        conn.close()


def revoke_confirmation(user_id: Any, confirmation_token: str) -> dict:
    """Withdraw an approval before it is redeemed. Only its own actor may revoke it."""
    _svc._require_enabled()
    th = _token_hash(confirmation_token)
    conn = _db.connect()
    try:
        _ensure_confirm_table(conn)
        cur = conn.execute(
            f"UPDATE {_CONFIRM_TABLE} SET status = 'revoked' "
            "WHERE token_hash = ? AND user_id = ? AND status = 'pending'",
            (th, _svc._sid(user_id)))
        revoked = int(getattr(cur, "rowcount", 0) or 0) == 1
        conn.commit()
    finally:
        conn.close()
    return {"ok": True, "revoked": revoked}


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
    token, expires_at = _mint_confirmation(user_id, tool, canonical)
    return {
        "tool": tool, "requires_confirmation": True, "write": True,
        "risk": spec.get("risk", "high"), "canonical_params": canonical,
        "summary": spec.get("summary"), "confirmation_token": token,
        "expires_at": expires_at, "ttl_seconds": _ttl_seconds(),
        "single_use": True,
    }


def execute(user_id: Any, tool: str, params: Optional[dict] = None, *,
            confirmation_token: Optional[str] = None) -> dict:
    """Phase 2. A confirmation-gated tool REQUIRES a token matching a freshly-computed
    token for these exact canonical params (428 without, 409 on mismatch). After a
    write the canonical state is RE-READ and ``verified`` reflects observed truth."""
    _svc._require_enabled()
    spec = _spec(tool)
    params = params or {}

    if not spec.get("write"):
        result = spec["read"](user_id, params)
        return {"ok": True, "tool": tool, "write": False, "result": result}

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

    return {"ok": True, "tool": tool, "write": True, "verified": bool(ok),
            "observed": observed, "canonical_params": canonical}
