"""Business OS — Section 2: Store framework-agnostic HTTP controller.

bot.py owns authentication, CSRF, and session identity; it then calls these pure
functions with an *already-authenticated* ``actor_user_id`` and parsed input, and
turns the returned ``(status_code, body)`` tuple into a Flask JSON response. Keeping
the decision logic here (not inline in bot.py) makes every branch unit-testable
without importing Flask (bot.py is not importable in the hermetic sandbox).

Contract for every handler (mirrors the Business HQ / marketplace controllers):

  * returns ``(int status_code, dict body)``; ``body`` always has an ``ok`` bool;
  * the whole canonical surface is DARK when ``BUSINESS_OS_STORE`` is off — every
    authenticated handler returns 404 so no partial canonical path is exposed;
  * access is enforced in the service (no role on the business ⇒ 404, existence not
    leaked);
  * only the curated ``StoreError`` message is surfaced — never an internal
    exception string;
  * clients may never set ``storefront_id`` / ``product_id`` / ``collection_id`` /
    ``business_id`` / ``status`` / timestamps directly; those are server-authoritative.

``actor_user_id`` is passed in by bot.py from the session/token — never from the
request body. ``context`` carries fresh ``{account_status, access_enabled}`` so
account-hold precedence is evaluated live.

The lone exception to the dark-404 rule is :func:`public_storefront`, the anonymous
read-only projection — but it too returns 404 when the flag is off, and 404 when there
is no *published* storefront, so nothing about an unpublished store leaks.
"""

from __future__ import annotations

from typing import Any, Optional, Tuple

from services.business_os.store import service as svc
from services.business_os.store.service import StoreError


# --- client-writable field allowlists (anything else is ignored) ------------
STOREFRONT_FIELDS = set(svc.STOREFRONT_FIELDS)
PRODUCT_CREATE_FIELDS = set(svc.PRODUCT_CREATE_FIELDS)
PRODUCT_UPDATE_FIELDS = set(svc.PRODUCT_UPDATE_FIELDS)
COLLECTION_FIELDS = set(svc.COLLECTION_FIELDS)


def _ok(body: dict, status: int = 200) -> Tuple[int, dict]:
    out = {"ok": True}
    out.update(body or {})
    return (status, out)


def _err(exc: StoreError) -> Tuple[int, dict]:
    return (int(getattr(exc, "http_status", 400)),
            {"ok": False, "error": str(exc),
             "code": getattr(exc, "code", "invalid")})


def _dark() -> Tuple[int, dict]:
    """Uniform 404 when the flag is off — no canonical path is revealed."""
    return (404, {"ok": False, "error": "Not found.", "code": "not_found"})


def _pick(payload: Any, allowed: set) -> dict:
    if not isinstance(payload, dict):
        return {}
    return {k: v for k, v in payload.items() if k in allowed}


def _guard() -> bool:
    return svc.is_enabled()


# ============================================================================
# Storefront
# ============================================================================
def get_storefront(actor_user_id: Any, business_id: str) -> Tuple[int, dict]:
    if not _guard():
        return _dark()
    try:
        return _ok({"storefront": svc.get_storefront(business_id, actor_user_id)})
    except StoreError as e:
        return _err(e)


def upsert_storefront(actor_user_id: Any, business_id: str, payload: Any,
                      *, context: Optional[dict] = None) -> Tuple[int, dict]:
    if not _guard():
        return _dark()
    try:
        sf = svc.upsert_storefront(business_id, actor_user_id,
                                   _pick(payload, STOREFRONT_FIELDS), context=context)
        return _ok({"storefront": sf})
    except StoreError as e:
        return _err(e)


def set_storefront_status(actor_user_id: Any, business_id: str, payload: Any,
                          *, context: Optional[dict] = None) -> Tuple[int, dict]:
    if not _guard():
        return _dark()
    payload = payload if isinstance(payload, dict) else {}
    action = payload.get("action")
    reason = payload.get("reason")
    try:
        sf = svc.set_storefront_status(business_id, actor_user_id, action,
                                       reason=reason, context=context)
        return _ok({"storefront": sf})
    except StoreError as e:
        return _err(e)


# ============================================================================
# Products
# ============================================================================
def create_product(actor_user_id: Any, business_id: str, payload: Any,
                   *, context: Optional[dict] = None) -> Tuple[int, dict]:
    if not _guard():
        return _dark()
    try:
        p = svc.create_product(business_id, actor_user_id,
                               _pick(payload, PRODUCT_CREATE_FIELDS), context=context)
        return _ok({"product": p}, status=201)
    except StoreError as e:
        return _err(e)


def get_product(actor_user_id: Any, business_id: str,
                product_id: str) -> Tuple[int, dict]:
    if not _guard():
        return _dark()
    try:
        return _ok({"product": svc.get_product(business_id, actor_user_id, product_id)})
    except StoreError as e:
        return _err(e)


def list_products(actor_user_id: Any, business_id: str,
                  *, status: Optional[str] = None) -> Tuple[int, dict]:
    if not _guard():
        return _dark()
    try:
        return _ok({"products": svc.list_products(business_id, actor_user_id,
                                                  status=status)})
    except StoreError as e:
        return _err(e)


def update_product(actor_user_id: Any, business_id: str, product_id: str,
                   payload: Any, *, context: Optional[dict] = None) -> Tuple[int, dict]:
    if not _guard():
        return _dark()
    try:
        p = svc.update_product(business_id, actor_user_id, product_id,
                               _pick(payload, PRODUCT_UPDATE_FIELDS), context=context)
        return _ok({"product": p})
    except StoreError as e:
        return _err(e)


def set_product_status(actor_user_id: Any, business_id: str, product_id: str,
                       payload: Any, *, context: Optional[dict] = None) -> Tuple[int, dict]:
    if not _guard():
        return _dark()
    payload = payload if isinstance(payload, dict) else {}
    action = payload.get("action")
    reason = payload.get("reason")
    try:
        p = svc.set_product_status(business_id, actor_user_id, product_id, action,
                                   reason=reason, context=context)
        return _ok({"product": p})
    except StoreError as e:
        return _err(e)


# ============================================================================
# Collections
# ============================================================================
def create_collection(actor_user_id: Any, business_id: str, payload: Any,
                       *, context: Optional[dict] = None) -> Tuple[int, dict]:
    if not _guard():
        return _dark()
    try:
        c = svc.create_collection(business_id, actor_user_id,
                                  _pick(payload, COLLECTION_FIELDS), context=context)
        return _ok({"collection": c}, status=201)
    except StoreError as e:
        return _err(e)


def list_collections(actor_user_id: Any, business_id: str) -> Tuple[int, dict]:
    if not _guard():
        return _dark()
    try:
        return _ok({"collections": svc.list_collections(business_id, actor_user_id)})
    except StoreError as e:
        return _err(e)


def add_product_to_collection(actor_user_id: Any, business_id: str, collection_id: str,
                              payload: Any, *,
                              context: Optional[dict] = None) -> Tuple[int, dict]:
    if not _guard():
        return _dark()
    payload = payload if isinstance(payload, dict) else {}
    product_id = payload.get("product_id")
    if product_id is None:
        return _err(StoreError("product_id is required.", 400, "invalid"))
    try:
        r = svc.add_product_to_collection(business_id, actor_user_id, collection_id,
                                          product_id, context=context)
        return _ok({"membership": r}, status=201)
    except StoreError as e:
        return _err(e)


def remove_product_from_collection(actor_user_id: Any, business_id: str,
                                   collection_id: str, product_id: str,
                                   *, context: Optional[dict] = None) -> Tuple[int, dict]:
    if not _guard():
        return _dark()
    try:
        r = svc.remove_product_from_collection(business_id, actor_user_id,
                                               collection_id, product_id,
                                               context=context)
        return _ok({"membership": r})
    except StoreError as e:
        return _err(e)


def list_collection_products(actor_user_id: Any, business_id: str,
                             collection_id: str) -> Tuple[int, dict]:
    if not _guard():
        return _dark()
    try:
        return _ok({"products": svc.list_collection_products(
            business_id, actor_user_id, collection_id)})
    except StoreError as e:
        return _err(e)


# ============================================================================
# Public storefront (anonymous, read-only)
# ============================================================================
def public_storefront(business_id: str) -> Tuple[int, dict]:
    if not _guard():
        return _dark()
    try:
        sf = svc.public_storefront(business_id)
        if sf is None:
            return (404, {"ok": False, "error": "Not found.", "code": "not_found"})
        return _ok({"storefront": sf})
    except StoreError as e:
        return _err(e)


# ============================================================================
# Timeline
# ============================================================================
def get_timeline(actor_user_id: Any, business_id: str,
                *, limit: int = 100) -> Tuple[int, dict]:
    if not _guard():
        return _dark()
    try:
        return _ok({"timeline": svc.get_timeline(business_id, actor_user_id,
                                                 limit=limit)})
    except StoreError as e:
        return _err(e)
