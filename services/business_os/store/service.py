"""Business OS — Section 2: Store service (flag-gated pure logic).

The canonical storefront + catalog logic for a Section-1 Business. Every access
decision is resolved against S1 canonical membership/RBAC (imported, never re-modeled):
this module reads the caller's effective role on the business via
``business.service._effective_role`` and compares it against a Store-local permission
matrix using the same role-rank ordering. There is exactly ONE identity/permission
system and it lives in S1.

Contract mirrors the other Business OS services:

  * flag-gated: everything raises ``StoreError(..., 503, "disabled")`` when
    ``BUSINESS_OS_STORE`` is off;
  * account hold beats every write (``403 account_hold``) via the live ``context``;
  * access is enforced against the S1 business; a caller with no role on the business
    gets ``404 not_found`` (existence is not leaked);
  * server-authoritative ids/status/timestamps — clients never set them;
  * every mutation writes an append-only audit row.

Explicitly out of scope (reuse, don't duplicate): orders, carts, checkout, payments,
payouts. Those are the Orders / Payments canonical domains.
"""

from __future__ import annotations

import json
import os
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from services import db
from services.business_os.store import schema as store_schema
from services.business_os.business import service as biz_svc


FLAG_ENV = "BUSINESS_OS_STORE"


def is_enabled() -> bool:
    raw = (os.environ.get(FLAG_ENV) or "").strip().lower()
    return raw in {"1", "true", "on", "yes", "enabled", "canonical"}


# --- vocabularies -----------------------------------------------------------
STOREFRONT_STATUSES = {"draft", "published", "suspended", "archived"}
STOREFRONT_ACTIONS = {
    "publish": "published",
    "suspend": "suspended",
    "restore": "published",
    "archive": "archived",
}
STOREFRONT_TRANSITIONS = {
    "draft": {"published", "archived"},
    "published": {"suspended", "archived"},
    "suspended": {"published", "archived"},
    "archived": set(),
}

PRODUCT_STATUSES = {"draft", "active", "archived"}
PRODUCT_ACTIONS = {"activate": "active", "draft": "draft", "archive": "archived"}
PRODUCT_TRANSITIONS = {
    "draft": {"active", "archived"},
    "active": {"draft", "archived"},
    "archived": set(),
}

COLLECTION_STATUSES = {"active", "archived"}

# Store permissions expressed as the MIN S1 role required (same rank order as S1).
PERMISSIONS = {
    "store.read": "viewer",
    "store.manage": "manager",   # products, collections, storefront settings
    "store.publish": "admin",    # storefront lifecycle (publish/suspend/archive)
}

# --- field limits / formats -------------------------------------------------
NAME_MAX = 160
HEADLINE_MAX = 200
ABOUT_MAX = 8000
DESC_MAX = 8000
SKU_MAX = 80
SLUG_MAX = 80
CURRENCY_RE = re.compile(r"^[A-Z]{3}$")
SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
THEME_JSON_MAX = 20000
PRICE_MAX = 10_000_000_00  # 10M in minor units — sane upper bound

# Client-writable field allowlists (server-authoritative fields never appear here).
STOREFRONT_FIELDS = ("name", "slug", "headline", "about", "theme", "currency")
PRODUCT_CREATE_FIELDS = ("title", "subtitle", "description", "price_cents",
                         "currency", "sku", "media_ref", "inventory_qty")
PRODUCT_UPDATE_FIELDS = PRODUCT_CREATE_FIELDS
COLLECTION_FIELDS = ("title", "slug", "description")


class StoreError(ValueError):
    def __init__(self, message: str, http_status: int = 400, code: str = "invalid"):
        super().__init__(message)
        self.http_status = int(http_status)
        self.code = code


# --- small helpers ----------------------------------------------------------
def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _uid() -> str:
    return uuid.uuid4().hex


def _sid(v: Any) -> str:
    return str(v)


def _require_enabled() -> None:
    if not is_enabled():
        raise StoreError("Store is not enabled in this environment.", 503, "disabled")


def _require_not_held(context: Optional[dict]) -> None:
    ctx = context or {}
    status = str(ctx.get("account_status") or "").lower()
    access = ctx.get("access_enabled")
    if status in {"suspended", "banned", "disabled", "hold"}:
        raise StoreError("Account is on hold.", 403, "account_hold")
    if access is not None and not access:
        raise StoreError("Account access is disabled.", 403, "account_hold")


def _row(row) -> Optional[dict]:
    if row is None:
        return None
    try:
        return dict(row)
    except Exception:
        return None


def _rows(rows) -> list:
    out = []
    for r in rows or []:
        d = _row(r)
        if d is not None:
            out.append(d)
    return out


def _audit(conn, *, business_id, subject_type, subject_ref, action, actor,
           reason=None, before=None, after=None) -> None:
    conn.execute(
        "INSERT INTO business_os_store_audit "
        "(business_id, subject_type, subject_ref, action, actor, reason, "
        "before_json, after_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            None if business_id is None else str(business_id),
            subject_type,
            None if subject_ref is None else str(subject_ref),
            action,
            None if actor is None else str(actor),
            reason,
            None if before is None else json.dumps(before, sort_keys=True),
            None if after is None else json.dumps(after, sort_keys=True),
            _now_iso(),
        ),
    )


# --- authorization (reuse S1 RBAC — single source of truth) -----------------
def _require_biz_permission(conn, business_id: str, actor_user_id: Any,
                            permission: str) -> str:
    """Resolve the caller's S1 effective role on the business and enforce a Store
    permission against it. Raises 404 if the caller has no role (existence not leaked),
    403 if the role is insufficient. Returns the effective role."""
    role = biz_svc._effective_role(conn, business_id, actor_user_id)
    if role is None:
        raise StoreError("Store not found.", 404, "not_found")
    needed = PERMISSIONS.get(permission)
    if needed is None:
        raise StoreError("Unknown permission.", 403, "forbidden")
    if biz_svc._role_rank(role) > biz_svc._role_rank(needed):
        raise StoreError(
            f"Your role ({role}) cannot perform this action.", 403, "forbidden")
    return role


# --- validation -------------------------------------------------------------
def _clean_str(value: Any, *, field: str, max_len: int,
               required: bool = False) -> Optional[str]:
    if value is None:
        if required:
            raise StoreError(f"{field} is required.", 400, "invalid")
        return None
    if not isinstance(value, str):
        raise StoreError(f"{field} must be text.", 400, "invalid")
    v = value.strip()
    if not v:
        if required:
            raise StoreError(f"{field} is required.", 400, "invalid")
        return None
    if len(v) > max_len:
        raise StoreError(f"{field} is too long (max {max_len}).", 400, "invalid")
    return v


def _clean_slug(value: Any, *, field: str = "slug") -> Optional[str]:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if not isinstance(value, str):
        raise StoreError(f"{field} must be text.", 400, "invalid")
    v = value.strip().lower()
    if len(v) > SLUG_MAX or not SLUG_RE.match(v):
        raise StoreError(
            f"{field} must be lowercase letters, numbers and hyphens.", 400, "invalid")
    return v


def _clean_currency(value: Any, *, default: Optional[str] = None) -> Optional[str]:
    if value is None or (isinstance(value, str) and not value.strip()):
        return default
    if not isinstance(value, str) or not CURRENCY_RE.match(value.strip().upper()):
        raise StoreError("currency must be a 3-letter ISO code like USD.", 400, "invalid")
    return value.strip().upper()


def _clean_price(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise StoreError("price_cents must be an integer number of minor units.",
                         400, "invalid")
    if value < 0:
        raise StoreError("price_cents cannot be negative.", 400, "invalid")
    if value > PRICE_MAX:
        raise StoreError("price_cents is too large.", 400, "invalid")
    return value


def _clean_inventory(value: Any) -> Optional[int]:
    if value is None:
        return None  # untracked / unlimited
    if isinstance(value, bool) or not isinstance(value, int):
        raise StoreError("inventory_qty must be an integer or null.", 400, "invalid")
    if value < 0:
        raise StoreError("inventory_qty cannot be negative.", 400, "invalid")
    return value


# --- projections ------------------------------------------------------------
def _storefront_public(row: dict) -> dict:
    theme = row.get("theme_json")
    parsed = None
    if theme:
        try:
            parsed = json.loads(theme)
        except Exception:
            parsed = None
    return {
        "storefront_id": row.get("storefront_id"),
        "business_id": row.get("business_id"),
        "slug": row.get("slug"),
        "name": row.get("name"),
        "headline": row.get("headline"),
        "about": row.get("about"),
        "theme": parsed,
        "currency": row.get("currency"),
        "status": row.get("status"),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }


def _product_public(row: dict) -> dict:
    return {
        "product_id": row.get("product_id"),
        "business_id": row.get("business_id"),
        "title": row.get("title"),
        "subtitle": row.get("subtitle"),
        "description": row.get("description"),
        "price_cents": row.get("price_cents"),
        "currency": row.get("currency"),
        "sku": row.get("sku"),
        "media_ref": row.get("media_ref"),
        "inventory_qty": row.get("inventory_qty"),
        "status": row.get("status"),
        "position": row.get("position"),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }


def _collection_public(row: dict) -> dict:
    return {
        "collection_id": row.get("collection_id"),
        "business_id": row.get("business_id"),
        "title": row.get("title"),
        "slug": row.get("slug"),
        "description": row.get("description"),
        "status": row.get("status"),
        "position": row.get("position"),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }


# --- row fetch helpers ------------------------------------------------------
def _get_storefront_row(conn, business_id: str) -> Optional[dict]:
    return _row(conn.execute(
        "SELECT * FROM business_os_store_storefront WHERE business_id = ?",
        (business_id,)).fetchone())


def _get_product_row(conn, business_id: str, product_id: str) -> Optional[dict]:
    return _row(conn.execute(
        "SELECT * FROM business_os_store_products "
        "WHERE product_id = ? AND business_id = ?",
        (product_id, business_id)).fetchone())


def _get_collection_row(conn, business_id: str, collection_id: str) -> Optional[dict]:
    return _row(conn.execute(
        "SELECT * FROM business_os_store_collections "
        "WHERE collection_id = ? AND business_id = ?",
        (collection_id, business_id)).fetchone())


# ============================================================================
# Storefront
# ============================================================================
def get_storefront(business_id: str, actor_user_id: Any) -> Optional[dict]:
    _require_enabled()
    conn = db.connect()
    try:
        _require_biz_permission(conn, business_id, actor_user_id, "store.read")
        row = _get_storefront_row(conn, business_id)
        return _storefront_public(row) if row else None
    finally:
        conn.close()


def upsert_storefront(business_id: str, actor_user_id: Any, payload: dict,
                      *, context: Optional[dict] = None) -> dict:
    """Create the business's storefront (draft) or update its settings. One per
    business; server owns id/status/timestamps."""
    _require_enabled()
    _require_not_held(context)
    if not isinstance(payload, dict):
        raise StoreError("Payload must be an object.", 400, "invalid")
    conn = db.connect()
    try:
        _require_biz_permission(conn, business_id, actor_user_id, "store.manage")
        existing = _get_storefront_row(conn, business_id)

        name = _clean_str(payload.get("name"), field="name", max_len=NAME_MAX,
                          required=existing is None)
        slug = _clean_slug(payload.get("slug")) if "slug" in payload else (
            existing.get("slug") if existing else None)
        headline = _clean_str(payload.get("headline"), field="headline",
                              max_len=HEADLINE_MAX) if "headline" in payload else (
            existing.get("headline") if existing else None)
        about = _clean_str(payload.get("about"), field="about",
                           max_len=ABOUT_MAX) if "about" in payload else (
            existing.get("about") if existing else None)
        currency = _clean_currency(
            payload.get("currency"),
            default=(existing.get("currency") if existing else "USD"))

        theme_json = existing.get("theme_json") if existing else None
        if "theme" in payload:
            theme = payload.get("theme")
            if theme is None:
                theme_json = None
            else:
                try:
                    theme_json = json.dumps(theme, sort_keys=True)
                except (TypeError, ValueError):
                    raise StoreError("theme must be JSON-serializable.", 400, "invalid")
                if len(theme_json) > THEME_JSON_MAX:
                    raise StoreError("theme is too large.", 400, "invalid")

        # slug uniqueness (excluding self)
        if slug is not None:
            clash = _row(conn.execute(
                "SELECT business_id FROM business_os_store_storefront WHERE slug = ?",
                (slug,)).fetchone())
            if clash and _sid(clash.get("business_id")) != _sid(business_id):
                raise StoreError("That storefront slug is already taken.", 409, "conflict")

        now = _now_iso()
        if existing is None:
            sid = "sf_" + _uid()
            conn.execute(
                "INSERT INTO business_os_store_storefront "
                "(storefront_id, business_id, slug, name, headline, about, theme_json, "
                "currency, status, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'draft', ?, ?)",
                (sid, _sid(business_id), slug, name, headline, about, theme_json,
                 currency, now, now),
            )
            _audit(conn, business_id=business_id, subject_type="storefront",
                   subject_ref=sid, action="storefront.create",
                   actor=actor_user_id, after={"name": name, "slug": slug})
        else:
            conn.execute(
                "UPDATE business_os_store_storefront SET name = ?, slug = ?, "
                "headline = ?, about = ?, theme_json = ?, currency = ?, updated_at = ? "
                "WHERE business_id = ?",
                (name if name is not None else existing.get("name"), slug, headline,
                 about, theme_json, currency, now, _sid(business_id)),
            )
            _audit(conn, business_id=business_id, subject_type="storefront",
                   subject_ref=existing.get("storefront_id"),
                   action="storefront.update", actor=actor_user_id,
                   before={"name": existing.get("name"), "slug": existing.get("slug")},
                   after={"name": name, "slug": slug})
        conn.commit()
        return _storefront_public(_get_storefront_row(conn, business_id))
    finally:
        conn.close()


def set_storefront_status(business_id: str, actor_user_id: Any, action: str,
                          *, reason: Optional[str] = None,
                          context: Optional[dict] = None) -> dict:
    _require_enabled()
    _require_not_held(context)
    target = STOREFRONT_ACTIONS.get(action)
    if target is None:
        raise StoreError("Unknown storefront action.", 400, "invalid")
    conn = db.connect()
    try:
        _require_biz_permission(conn, business_id, actor_user_id, "store.publish")
        row = _get_storefront_row(conn, business_id)
        if row is None:
            raise StoreError("Storefront not found.", 404, "not_found")
        cur = str(row.get("status"))
        if target not in STOREFRONT_TRANSITIONS.get(cur, set()):
            raise StoreError(
                f"Cannot {action} a storefront that is {cur}.", 409, "conflict")
        now = _now_iso()
        conn.execute(
            "UPDATE business_os_store_storefront SET status = ?, updated_at = ? "
            "WHERE business_id = ?",
            (target, now, _sid(business_id)),
        )
        _audit(conn, business_id=business_id, subject_type="storefront",
               subject_ref=row.get("storefront_id"),
               action=f"storefront.{action}", actor=actor_user_id, reason=reason,
               before={"status": cur}, after={"status": target})
        conn.commit()
        return _storefront_public(_get_storefront_row(conn, business_id))
    finally:
        conn.close()


# ============================================================================
# Products
# ============================================================================
def create_product(business_id: str, actor_user_id: Any, payload: dict,
                   *, context: Optional[dict] = None) -> dict:
    _require_enabled()
    _require_not_held(context)
    if not isinstance(payload, dict):
        raise StoreError("Payload must be an object.", 400, "invalid")
    conn = db.connect()
    try:
        _require_biz_permission(conn, business_id, actor_user_id, "store.manage")
        sf = _get_storefront_row(conn, business_id)
        default_currency = sf.get("currency") if sf else "USD"

        title = _clean_str(payload.get("title"), field="title",
                           max_len=NAME_MAX, required=True)
        subtitle = _clean_str(payload.get("subtitle"), field="subtitle", max_len=NAME_MAX)
        description = _clean_str(payload.get("description"), field="description",
                                 max_len=DESC_MAX)
        price = _clean_price(payload.get("price_cents", 0))
        currency = _clean_currency(payload.get("currency"), default=default_currency)
        sku = _clean_str(payload.get("sku"), field="sku", max_len=SKU_MAX)
        media_ref = _clean_str(payload.get("media_ref"), field="media_ref", max_len=512)
        inventory = _clean_inventory(payload.get("inventory_qty"))

        pid = "prod_" + _uid()
        now = _now_iso()
        conn.execute(
            "INSERT INTO business_os_store_products "
            "(product_id, business_id, title, subtitle, description, price_cents, "
            "currency, sku, media_ref, inventory_qty, status, position, created_at, "
            "updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'draft', 0, ?, ?)",
            (pid, _sid(business_id), title, subtitle, description, price, currency,
             sku, media_ref, inventory, now, now),
        )
        _audit(conn, business_id=business_id, subject_type="product", subject_ref=pid,
               action="product.create", actor=actor_user_id,
               after={"title": title, "price_cents": price})
        conn.commit()
        return _product_public(_get_product_row(conn, business_id, pid))
    finally:
        conn.close()


def get_product(business_id: str, actor_user_id: Any, product_id: str) -> dict:
    _require_enabled()
    conn = db.connect()
    try:
        _require_biz_permission(conn, business_id, actor_user_id, "store.read")
        row = _get_product_row(conn, business_id, product_id)
        if row is None:
            raise StoreError("Product not found.", 404, "not_found")
        return _product_public(row)
    finally:
        conn.close()


def list_products(business_id: str, actor_user_id: Any,
                  *, status: Optional[str] = None) -> list:
    _require_enabled()
    conn = db.connect()
    try:
        _require_biz_permission(conn, business_id, actor_user_id, "store.read")
        if status is not None:
            if status not in PRODUCT_STATUSES:
                raise StoreError("Unknown product status.", 400, "invalid")
            rows = conn.execute(
                "SELECT * FROM business_os_store_products "
                "WHERE business_id = ? AND status = ? "
                "ORDER BY position ASC, created_at ASC",
                (_sid(business_id), status)).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM business_os_store_products WHERE business_id = ? "
                "ORDER BY position ASC, created_at ASC",
                (_sid(business_id),)).fetchall()
        return [_product_public(r) for r in _rows(rows)]
    finally:
        conn.close()


def update_product(business_id: str, actor_user_id: Any, product_id: str,
                   payload: dict, *, context: Optional[dict] = None) -> dict:
    _require_enabled()
    _require_not_held(context)
    if not isinstance(payload, dict):
        raise StoreError("Payload must be an object.", 400, "invalid")
    conn = db.connect()
    try:
        _require_biz_permission(conn, business_id, actor_user_id, "store.manage")
        row = _get_product_row(conn, business_id, product_id)
        if row is None:
            raise StoreError("Product not found.", 404, "not_found")

        updates: dict = {}
        if "title" in payload:
            updates["title"] = _clean_str(payload.get("title"), field="title",
                                          max_len=NAME_MAX, required=True)
        if "subtitle" in payload:
            updates["subtitle"] = _clean_str(payload.get("subtitle"),
                                             field="subtitle", max_len=NAME_MAX)
        if "description" in payload:
            updates["description"] = _clean_str(payload.get("description"),
                                                field="description", max_len=DESC_MAX)
        if "price_cents" in payload:
            updates["price_cents"] = _clean_price(payload.get("price_cents"))
        if "currency" in payload:
            updates["currency"] = _clean_currency(payload.get("currency"),
                                                  default=row.get("currency"))
        if "sku" in payload:
            updates["sku"] = _clean_str(payload.get("sku"), field="sku", max_len=SKU_MAX)
        if "media_ref" in payload:
            updates["media_ref"] = _clean_str(payload.get("media_ref"),
                                              field="media_ref", max_len=512)
        if "inventory_qty" in payload:
            updates["inventory_qty"] = _clean_inventory(payload.get("inventory_qty"))
        if not updates:
            return _product_public(row)

        now = _now_iso()
        cols = ", ".join(f"{k} = ?" for k in updates)
        conn.execute(
            f"UPDATE business_os_store_products SET {cols}, updated_at = ? "
            "WHERE product_id = ? AND business_id = ?",
            tuple(updates.values()) + (now, product_id, _sid(business_id)),
        )
        _audit(conn, business_id=business_id, subject_type="product",
               subject_ref=product_id, action="product.update", actor=actor_user_id,
               before={k: row.get(k) for k in updates}, after=updates)
        conn.commit()
        return _product_public(_get_product_row(conn, business_id, product_id))
    finally:
        conn.close()


def set_product_status(business_id: str, actor_user_id: Any, product_id: str,
                       action: str, *, reason: Optional[str] = None,
                       context: Optional[dict] = None) -> dict:
    _require_enabled()
    _require_not_held(context)
    target = PRODUCT_ACTIONS.get(action)
    if target is None:
        raise StoreError("Unknown product action.", 400, "invalid")
    conn = db.connect()
    try:
        _require_biz_permission(conn, business_id, actor_user_id, "store.manage")
        row = _get_product_row(conn, business_id, product_id)
        if row is None:
            raise StoreError("Product not found.", 404, "not_found")
        cur = str(row.get("status"))
        if target == cur:
            raise StoreError(f"Product is already {cur}.", 409, "conflict")
        if target not in PRODUCT_TRANSITIONS.get(cur, set()):
            raise StoreError(
                f"Cannot move a product from {cur} to {target}.", 409, "conflict")
        now = _now_iso()
        conn.execute(
            "UPDATE business_os_store_products SET status = ?, updated_at = ? "
            "WHERE product_id = ? AND business_id = ?",
            (target, now, product_id, _sid(business_id)),
        )
        _audit(conn, business_id=business_id, subject_type="product",
               subject_ref=product_id, action=f"product.{action}",
               actor=actor_user_id, reason=reason,
               before={"status": cur}, after={"status": target})
        conn.commit()
        return _product_public(_get_product_row(conn, business_id, product_id))
    finally:
        conn.close()


# ============================================================================
# Collections
# ============================================================================
def create_collection(business_id: str, actor_user_id: Any, payload: dict,
                       *, context: Optional[dict] = None) -> dict:
    _require_enabled()
    _require_not_held(context)
    if not isinstance(payload, dict):
        raise StoreError("Payload must be an object.", 400, "invalid")
    conn = db.connect()
    try:
        _require_biz_permission(conn, business_id, actor_user_id, "store.manage")
        title = _clean_str(payload.get("title"), field="title",
                           max_len=NAME_MAX, required=True)
        slug = _clean_slug(payload.get("slug")) if "slug" in payload else None
        description = _clean_str(payload.get("description"), field="description",
                                 max_len=DESC_MAX)
        cid = "col_" + _uid()
        now = _now_iso()
        conn.execute(
            "INSERT INTO business_os_store_collections "
            "(collection_id, business_id, title, slug, description, status, position, "
            "created_at, updated_at) VALUES (?, ?, ?, ?, ?, 'active', 0, ?, ?)",
            (cid, _sid(business_id), title, slug, description, now, now),
        )
        _audit(conn, business_id=business_id, subject_type="collection",
               subject_ref=cid, action="collection.create", actor=actor_user_id,
               after={"title": title})
        conn.commit()
        return _collection_public(_get_collection_row(conn, business_id, cid))
    finally:
        conn.close()


def list_collections(business_id: str, actor_user_id: Any) -> list:
    _require_enabled()
    conn = db.connect()
    try:
        _require_biz_permission(conn, business_id, actor_user_id, "store.read")
        rows = conn.execute(
            "SELECT * FROM business_os_store_collections WHERE business_id = ? "
            "ORDER BY position ASC, created_at ASC", (_sid(business_id),)).fetchall()
        return [_collection_public(r) for r in _rows(rows)]
    finally:
        conn.close()


def add_product_to_collection(business_id: str, actor_user_id: Any, collection_id: str,
                              product_id: str, *,
                              context: Optional[dict] = None) -> dict:
    _require_enabled()
    _require_not_held(context)
    conn = db.connect()
    try:
        _require_biz_permission(conn, business_id, actor_user_id, "store.manage")
        col = _get_collection_row(conn, business_id, collection_id)
        if col is None:
            raise StoreError("Collection not found.", 404, "not_found")
        prod = _get_product_row(conn, business_id, product_id)
        if prod is None:
            raise StoreError("Product not found.", 404, "not_found")
        existing = _row(conn.execute(
            "SELECT id FROM business_os_store_collection_products "
            "WHERE collection_id = ? AND product_id = ?",
            (collection_id, product_id)).fetchone())
        if existing:
            raise StoreError("Product is already in that collection.", 409, "conflict")
        now = _now_iso()
        conn.execute(
            "INSERT INTO business_os_store_collection_products "
            "(collection_id, product_id, position, created_at) VALUES (?, ?, 0, ?)",
            (collection_id, product_id, now),
        )
        _audit(conn, business_id=business_id, subject_type="collection",
               subject_ref=collection_id, action="collection.add_product",
               actor=actor_user_id, after={"product_id": product_id})
        conn.commit()
        return {"collection_id": collection_id, "product_id": product_id, "added": True}
    finally:
        conn.close()


def remove_product_from_collection(business_id: str, actor_user_id: Any,
                                   collection_id: str, product_id: str,
                                   *, context: Optional[dict] = None) -> dict:
    _require_enabled()
    _require_not_held(context)
    conn = db.connect()
    try:
        _require_biz_permission(conn, business_id, actor_user_id, "store.manage")
        col = _get_collection_row(conn, business_id, collection_id)
        if col is None:
            raise StoreError("Collection not found.", 404, "not_found")
        existing = _row(conn.execute(
            "SELECT id FROM business_os_store_collection_products "
            "WHERE collection_id = ? AND product_id = ?",
            (collection_id, product_id)).fetchone())
        if not existing:
            raise StoreError("Product is not in that collection.", 404, "not_found")
        conn.execute(
            "DELETE FROM business_os_store_collection_products "
            "WHERE collection_id = ? AND product_id = ?",
            (collection_id, product_id),
        )
        _audit(conn, business_id=business_id, subject_type="collection",
               subject_ref=collection_id, action="collection.remove_product",
               actor=actor_user_id, before={"product_id": product_id})
        conn.commit()
        return {"collection_id": collection_id, "product_id": product_id,
                "removed": True}
    finally:
        conn.close()


def list_collection_products(business_id: str, actor_user_id: Any,
                             collection_id: str) -> list:
    _require_enabled()
    conn = db.connect()
    try:
        _require_biz_permission(conn, business_id, actor_user_id, "store.read")
        col = _get_collection_row(conn, business_id, collection_id)
        if col is None:
            raise StoreError("Collection not found.", 404, "not_found")
        rows = conn.execute(
            "SELECT p.* FROM business_os_store_products p "
            "JOIN business_os_store_collection_products cp "
            "ON cp.product_id = p.product_id "
            "WHERE cp.collection_id = ? AND p.business_id = ? "
            "ORDER BY cp.position ASC, cp.id ASC",
            (collection_id, _sid(business_id))).fetchall()
        return [_product_public(r) for r in _rows(rows)]
    finally:
        conn.close()


# ============================================================================
# Public storefront (read-only projection: published storefront + active products)
# ============================================================================
def public_storefront(business_id: str) -> Optional[dict]:
    """Anonymous, read-only storefront view: only a *published* storefront and its
    *active* products. No RBAC (public), no account context. Returns None if there is
    no published storefront."""
    _require_enabled()
    conn = db.connect()
    try:
        sf = _get_storefront_row(conn, business_id)
        if sf is None or str(sf.get("status")) != "published":
            return None
        rows = conn.execute(
            "SELECT * FROM business_os_store_products "
            "WHERE business_id = ? AND status = 'active' "
            "ORDER BY position ASC, created_at ASC", (_sid(business_id),)).fetchall()
        out = _storefront_public(sf)
        out["products"] = [_product_public(r) for r in _rows(rows)]
        return out
    finally:
        conn.close()


def get_timeline(business_id: str, actor_user_id: Any, *, limit: int = 100) -> list:
    _require_enabled()
    conn = db.connect()
    try:
        _require_biz_permission(conn, business_id, actor_user_id, "store.read")
        try:
            lim = max(1, min(int(limit), 500))
        except (TypeError, ValueError):
            lim = 100
        rows = conn.execute(
            "SELECT * FROM business_os_store_audit WHERE business_id = ? "
            "ORDER BY id DESC LIMIT ?", (_sid(business_id), lim)).fetchall()
        out = []
        for r in _rows(rows):
            entry = dict(r)
            for k in ("before_json", "after_json"):
                if entry.get(k):
                    try:
                        entry[k[:-5]] = json.loads(entry[k])
                    except Exception:
                        entry[k[:-5]] = None
                else:
                    entry[k[:-5]] = None
                entry.pop(k, None)
            out.append(entry)
        return out
    finally:
        conn.close()
