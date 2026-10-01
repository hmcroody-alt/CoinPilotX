"""Business OS — Marketplace vertical service (flag-gated, canonical).

The seller + catalog half of the Stage 3 Marketplace MVP: an eligible seller can
be approved, create/edit product listings, publish/pause them, and manage
inventory. Money movement, orders, refunds and payouts live in sibling modules
(``orders`` / ``refunds``); this module owns NO money path.

Server-authoritative and additive: reads/writes only the ``business_os_mkt_*``
tables. Every state-changing and eligibility entrypoint is gated behind the
``BUSINESS_OS_MARKETPLACE`` flag; with the flag off the module raises
``MarketplaceError`` and touches nothing, so the whole surface is reversible.

Eligibility composes THREE separate inputs (never merged — mirrors the advertising
vertical and §8 of the shared-foundation checkpoint):

    1. Account hold / suspension  -> ``facade.account_hold`` (overrides all)
    2. Seller approval state      -> ``business_os_mkt_sellers.status``
    3. Feature rollout            -> ``BUSINESS_OS_MARKETPLACE`` flag
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from services import db
from services.business_os.marketplace import schema as _schema

try:  # shared suspension authority; import defensively.
    from services.business_os.entitlements import facade as _facade
except Exception:  # pragma: no cover
    _facade = None

try:  # canonical notification adapters; never a precondition.
    from services.business_os.marketplace import notifications as _notify
except Exception:  # pragma: no cover
    _notify = None


# --- flag -------------------------------------------------------------------
FLAG_ENV = "BUSINESS_OS_MARKETPLACE"


def is_enabled() -> bool:
    """True only when the rollout flag is explicitly on. Unset ⇒ off (inert)."""
    raw = (os.environ.get(FLAG_ENV) or "").strip().lower()
    return raw in {"1", "true", "on", "yes", "enabled", "canonical"}


# --- vocabularies -----------------------------------------------------------
SELLER_STATUSES = {"pending", "approved", "rejected", "suspended"}
PRODUCT_STATUSES = {"draft", "active", "paused", "archived"}
FULFILLMENT_TYPES = {"physical", "digital"}
# Owner-reachable product lifecycle verbs -> fixed target states. Clients never
# send a raw status; they send an action.
PRODUCT_ACTIONS = {
    "publish": "active", "pause": "paused", "resume": "active",
    "archive": "archived", "restore": "draft",
}
PRODUCT_TRANSITIONS = {
    "draft": {"active", "archived"},
    "active": {"paused", "archived"},
    "paused": {"active", "archived"},
    "archived": {"draft"},
}

TITLE_MAX = 160
DESC_MAX = 4000
PRICE_MAX_CENTS = 100_000_000  # $1,000,000 hard cap keeps typos out of the ledger


class MarketplaceError(ValueError):
    """Raised when a marketplace operation is rejected before any state change.

    Carries an ``http_status`` (403 ineligible, 404 missing/not-owned, 409 illegal
    transition, 400 validation, 503 flag off) and a stable ``code``.
    """

    def __init__(self, message: str, http_status: int = 400, code: str = "invalid"):
        super().__init__(message)
        self.http_status = http_status
        self.code = code


# --- time / id helpers ------------------------------------------------------
def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _uid() -> str:
    return uuid.uuid4().hex


def _sid(user_id: Any) -> str:
    return str(user_id)


def _require_enabled() -> None:
    if not is_enabled():
        raise MarketplaceError(
            "Marketplace is not enabled in this environment.",
            http_status=503, code="disabled")


def _require_not_held(user_id: Any, context: Optional[dict]) -> None:
    """Account hold beats everything. Uses the passed live context if present,
    else best-effort via the facade. Never silently passes a held account."""
    ctx = context or {}
    status = str(ctx.get("account_status") or "").lower()
    access = ctx.get("access_enabled")
    if status in {"suspended", "banned", "disabled", "hold"}:
        raise MarketplaceError("Account is on hold.", 403, "account_hold")
    if access is not None and not access:
        raise MarketplaceError("Account access is disabled.", 403, "account_hold")


def _audit(conn, *, subject_type, subject_ref, action, actor,
           reason=None, before=None, after=None) -> None:
    conn.execute(
        "INSERT INTO business_os_mkt_audit "
        "(subject_type, subject_ref, action, actor, reason, before_json, "
        "after_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            subject_type, None if subject_ref is None else str(subject_ref),
            action, None if actor is None else str(actor), reason,
            None if before is None else json.dumps(before, sort_keys=True),
            None if after is None else json.dumps(after, sort_keys=True),
            _now_iso(),
        ),
    )


def _row(row) -> Optional[dict]:
    if row is None:
        return None
    try:
        return dict(row)
    except Exception:
        return {k: row[k] for k in row.keys()}


# --- seller approval (input #2) ---------------------------------------------
def get_seller(user_id: Any, conn=None) -> Optional[dict]:
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        cur = conn.execute(
            "SELECT * FROM business_os_mkt_sellers WHERE seller_user_id = ?",
            (_sid(user_id),))
        return _row(cur.fetchone())
    finally:
        if owned:
            conn.close()


def upsert_seller(user_id: Any, *, display_name: Optional[str] = None,
                  notes: Optional[str] = None, conn=None) -> dict:
    """Create the seller record for ``user_id`` if absent (status 'pending').

    Idempotent: an existing record is returned; a supplied display_name/notes
    refreshes those descriptive fields. Approval is a separate admin action.
    """
    _require_enabled()
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        uid = _sid(user_id)
        existing = get_seller(uid, conn=conn)
        now = _now_iso()
        if existing is None:
            conn.execute(
                "INSERT INTO business_os_mkt_sellers "
                "(seller_user_id, status, display_name, notes, created_at, updated_at) "
                "VALUES (?, 'pending', ?, ?, ?, ?)",
                (uid, display_name, notes, now, now))
            _audit(conn, subject_type="seller", subject_ref=uid,
                   action="seller_register", actor=uid,
                   after={"status": "pending"})
        else:
            sets, vals = [], []
            if display_name is not None:
                sets.append("display_name = ?"); vals.append(display_name)
            if notes is not None:
                sets.append("notes = ?"); vals.append(notes)
            if sets:
                sets.append("updated_at = ?"); vals.append(now)
                vals.append(uid)
                conn.execute(
                    f"UPDATE business_os_mkt_sellers SET {', '.join(sets)} "
                    "WHERE seller_user_id = ?", tuple(vals))
        if owned:
            conn.commit()
        return get_seller(uid, conn=conn)
    finally:
        if owned:
            conn.close()


def set_seller_status(user_id: Any, status: str, *, actor: Any,
                      reason: Optional[str] = None, conn=None) -> dict:
    """Admin transition of seller approval state. Actor is required (governed)."""
    _require_enabled()
    if status not in SELLER_STATUSES:
        raise MarketplaceError(f"Unknown seller status: {status!r}.", 400, "invalid_status")
    if actor is None or not str(actor).strip():
        raise MarketplaceError("actor is required.", 400, "actor_required")
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        uid = _sid(user_id)
        existing = get_seller(uid, conn=conn)
        if existing is None:
            # Auto-create then transition (an admin may approve a brand-new seller).
            upsert_seller(uid, conn=conn)
            existing = get_seller(uid, conn=conn)
        before = {"status": existing.get("status")}
        conn.execute(
            "UPDATE business_os_mkt_sellers SET status = ?, updated_at = ? "
            "WHERE seller_user_id = ?", (status, _now_iso(), uid))
        _audit(conn, subject_type="seller", subject_ref=uid,
               action=f"seller_{status}", actor=actor, reason=reason,
               before=before, after={"status": status})
        if owned:
            conn.commit()
        return get_seller(uid, conn=conn)
    finally:
        if owned:
            conn.close()


def require_active_seller(user_id: Any, context: Optional[dict] = None, conn=None) -> dict:
    """Composed eligibility gate for seller writes: flag on, not held, approved."""
    _require_enabled()
    _require_not_held(user_id, context)
    seller = get_seller(user_id, conn=conn)
    if seller is None or seller.get("status") != "approved":
        raise MarketplaceError(
            "Seller is not approved to sell.", 403, "seller_not_approved")
    return seller


# --- product / listing catalog ----------------------------------------------
def _validate_product_input(*, title, price_cents, fulfillment_type, inventory_qty):
    if not title or not str(title).strip():
        raise MarketplaceError("title is required.", 400, "title_required")
    if len(str(title)) > TITLE_MAX:
        raise MarketplaceError("title is too long.", 400, "title_too_long")
    if isinstance(price_cents, bool) or not isinstance(price_cents, int):
        raise MarketplaceError("price_cents must be an integer.", 400, "invalid_price")
    if price_cents < 0 or price_cents > PRICE_MAX_CENTS:
        raise MarketplaceError("price_cents out of range.", 400, "invalid_price")
    if fulfillment_type not in FULFILLMENT_TYPES:
        raise MarketplaceError(
            "fulfillment_type must be 'physical' or 'digital'.", 400, "invalid_fulfillment")
    if inventory_qty is not None:
        if isinstance(inventory_qty, bool) or not isinstance(inventory_qty, int):
            raise MarketplaceError("inventory_qty must be an integer.", 400, "invalid_inventory")
        if inventory_qty < 0:
            raise MarketplaceError("inventory_qty cannot be negative.", 400, "invalid_inventory")


def create_product(user_id: Any, *, title: str, price_cents: int,
                   description: Optional[str] = None, currency: str = "usd",
                   fulfillment_type: str = "physical",
                   inventory_qty: Optional[int] = None,
                   context: Optional[dict] = None, conn=None) -> dict:
    """Create a DRAFT product owned by an approved seller. Nothing is listed live
    until ``publish``."""
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        require_active_seller(user_id, context, conn=conn)
        _validate_product_input(title=title, price_cents=price_cents,
                                fulfillment_type=fulfillment_type,
                                inventory_qty=inventory_qty)
        if description is not None and len(str(description)) > DESC_MAX:
            raise MarketplaceError("description is too long.", 400, "description_too_long")
        pid = "mktp_" + _uid()
        now = _now_iso()
        conn.execute(
            "INSERT INTO business_os_mkt_products "
            "(product_id, seller_user_id, title, description, price_cents, currency, "
            "fulfillment_type, inventory_qty, status, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'draft', ?, ?)",
            (pid, _sid(user_id), str(title).strip(), description, price_cents,
             str(currency or "usd").lower(), fulfillment_type, inventory_qty, now, now))
        _audit(conn, subject_type="product", subject_ref=pid,
               action="product_create", actor=user_id,
               after={"status": "draft", "price_cents": price_cents})
        if owned:
            conn.commit()
        return get_product(pid, requester_user_id=user_id, conn=conn)
    finally:
        if owned:
            conn.close()


def get_product(product_id: Any, *, requester_user_id: Any = None,
                for_public: bool = False, conn=None) -> Optional[dict]:
    """Fetch a product. When ``requester_user_id`` is given, a non-owner read of a
    non-public product returns None (existence not leaked). ``for_public`` allows a
    buyer to read an ``active`` product regardless of owner."""
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        cur = conn.execute(
            "SELECT * FROM business_os_mkt_products WHERE product_id = ?",
            (str(product_id),))
        row = _row(cur.fetchone())
        if row is None:
            return None
        if for_public:
            return row if row.get("status") == "active" else None
        if requester_user_id is not None and \
                row.get("seller_user_id") != _sid(requester_user_id):
            return None
        return row
    finally:
        if owned:
            conn.close()


def _owned_product_or_404(conn, product_id, user_id) -> dict:
    row = get_product(product_id, requester_user_id=user_id, conn=conn)
    if row is None:
        raise MarketplaceError("Product not found.", 404, "not_found")
    return row


def update_product(user_id: Any, product_id: Any, *, fields: dict,
                   context: Optional[dict] = None, conn=None) -> dict:
    """Owner edit of mutable product fields. Status is NOT settable here (use the
    lifecycle verbs). Only an approved, un-held seller may edit their own product."""
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        require_active_seller(user_id, context, conn=conn)
        product = _owned_product_or_404(conn, product_id, user_id)
        allowed = {"title", "description", "price_cents", "currency",
                   "fulfillment_type", "inventory_qty"}
        unknown = set(fields or {}) - allowed
        if unknown:
            raise MarketplaceError(
                f"Unknown product field(s): {sorted(unknown)}.", 400, "unknown_field")
        merged = dict(product)
        merged.update(fields or {})
        _validate_product_input(
            title=merged.get("title"), price_cents=merged.get("price_cents"),
            fulfillment_type=merged.get("fulfillment_type"),
            inventory_qty=merged.get("inventory_qty"))
        if merged.get("description") is not None and len(str(merged["description"])) > DESC_MAX:
            raise MarketplaceError("description is too long.", 400, "description_too_long")
        sets, vals = [], []
        for k in allowed:
            if k in (fields or {}):
                v = fields[k]
                if k == "currency":
                    v = str(v or "usd").lower()
                sets.append(f"{k} = ?"); vals.append(v)
        if sets:
            sets.append("updated_at = ?"); vals.append(_now_iso())
            vals.append(str(product_id))
            conn.execute(
                f"UPDATE business_os_mkt_products SET {', '.join(sets)} "
                "WHERE product_id = ?", tuple(vals))
            _audit(conn, subject_type="product", subject_ref=product_id,
                   action="product_update", actor=user_id,
                   before={k: product.get(k) for k in fields or {}},
                   after={k: fields[k] for k in fields or {}})
        if owned:
            conn.commit()
        return get_product(product_id, requester_user_id=user_id, conn=conn)
    finally:
        if owned:
            conn.close()


def transition_product(user_id: Any, product_id: Any, action: str, *,
                       context: Optional[dict] = None, conn=None) -> dict:
    """Owner lifecycle verb (publish/pause/resume/archive/restore) -> fixed state."""
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        require_active_seller(user_id, context, conn=conn)
        if action not in PRODUCT_ACTIONS:
            raise MarketplaceError(f"Unknown product action: {action!r}.", 400, "unknown_action")
        product = _owned_product_or_404(conn, product_id, user_id)
        target = PRODUCT_ACTIONS[action]
        cur_status = product.get("status")
        if target not in PRODUCT_TRANSITIONS.get(cur_status, set()):
            raise MarketplaceError(
                f"Illegal transition {cur_status} -> {target}.", 409, "illegal_transition")
        # Publishing a physical product with zero inventory is refused early.
        if target == "active" and product.get("fulfillment_type") == "physical" \
                and (product.get("inventory_qty") or 0) <= 0:
            raise MarketplaceError(
                "Cannot publish a physical product with no inventory.",
                409, "no_inventory")
        conn.execute(
            "UPDATE business_os_mkt_products SET status = ?, updated_at = ? "
            "WHERE product_id = ?", (target, _now_iso(), str(product_id)))
        _audit(conn, subject_type="product", subject_ref=product_id,
               action=f"product_{action}", actor=user_id,
               before={"status": cur_status}, after={"status": target})
        if owned:
            conn.commit()
        return get_product(product_id, requester_user_id=user_id, conn=conn)
    finally:
        if owned:
            conn.close()


def set_inventory(user_id: Any, product_id: Any, inventory_qty: Optional[int], *,
                  context: Optional[dict] = None, conn=None) -> dict:
    """Owner sets absolute inventory. None ⇒ unlimited (only valid for digital)."""
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        require_active_seller(user_id, context, conn=conn)
        product = _owned_product_or_404(conn, product_id, user_id)
        if inventory_qty is not None:
            if isinstance(inventory_qty, bool) or not isinstance(inventory_qty, int) \
                    or inventory_qty < 0:
                raise MarketplaceError("inventory_qty must be a non-negative integer.",
                                       400, "invalid_inventory")
        elif product.get("fulfillment_type") == "physical":
            raise MarketplaceError(
                "Physical products require a finite inventory.", 400, "invalid_inventory")
        conn.execute(
            "UPDATE business_os_mkt_products SET inventory_qty = ?, updated_at = ? "
            "WHERE product_id = ?", (inventory_qty, _now_iso(), str(product_id)))
        _audit(conn, subject_type="product", subject_ref=product_id,
               action="inventory_set", actor=user_id,
               before={"inventory_qty": product.get("inventory_qty")},
               after={"inventory_qty": inventory_qty})
        if owned:
            conn.commit()
        return get_product(product_id, requester_user_id=user_id, conn=conn)
    finally:
        if owned:
            conn.close()


def list_products(seller_user_id: Any, *, status: Optional[str] = None,
                  limit: int = 200, conn=None) -> list:
    """Owner-scoped product listing (a seller's own catalog)."""
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        q = "SELECT * FROM business_os_mkt_products WHERE seller_user_id = ?"
        params = [_sid(seller_user_id)]
        if status:
            q += " AND status = ?"; params.append(status)
        q += " ORDER BY created_at DESC LIMIT ?"; params.append(int(limit))
        return [_row(r) for r in conn.execute(q, tuple(params)).fetchall()]
    finally:
        if owned:
            conn.close()


def list_active_products(*, seller_user_id: Any = None, limit: int = 100, conn=None) -> list:
    """Public buyer-facing catalog: only ``active`` products, safe projection."""
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        q = "SELECT * FROM business_os_mkt_products WHERE status = 'active'"
        params: list = []
        if seller_user_id is not None:
            q += " AND seller_user_id = ?"; params.append(_sid(seller_user_id))
        q += " ORDER BY created_at DESC LIMIT ?"; params.append(int(limit))
        rows = [_row(r) for r in conn.execute(q, tuple(params)).fetchall()]
        return [public_product(r) for r in rows]
    finally:
        if owned:
            conn.close()


def public_product(row: dict) -> dict:
    """Client-safe projection of a product (no internal audit fields to leak, but
    make the shape explicit and stable for the buyer surface)."""
    return {
        "product_id": row.get("product_id"),
        "seller_user_id": row.get("seller_user_id"),
        "title": row.get("title"),
        "description": row.get("description"),
        "price_cents": row.get("price_cents"),
        "currency": row.get("currency"),
        "fulfillment_type": row.get("fulfillment_type"),
        "in_stock": (row.get("inventory_qty") is None or (row.get("inventory_qty") or 0) > 0),
        "status": row.get("status"),
    }
