"""Business OS — Store shipping/returns settings: framework-agnostic controller.

Same contract as ``store/api.py`` (see its module docstring): bot.py passes an
already-authenticated ``actor_user_id`` + parsed input; every handler returns
``(status_code, body)`` with an ``ok`` bool; the surface is DARK (404) when
``BUSINESS_OS_STORE`` is off; only curated ``StoreError`` messages surface;
ids/statuses/timestamps are server-authoritative.

Kept in its own module (not appended to ``api.py``) so the addition is purely
additive while other agents work the repo.

Intended mount (when bot.py is quiet enough to touch):

    GET    /api/business-os/store/<biz>/policies                    -> get_summary
    GET    /api/business-os/store/<biz>/shipping-profiles           -> list_profiles
    POST   /api/business-os/store/<biz>/shipping-profiles           -> create_profile
    GET    /api/business-os/store/<biz>/shipping-profiles/<id>      -> get_profile
    PATCH  /api/business-os/store/<biz>/shipping-profiles/<id>      -> update_profile
    POST   /api/business-os/store/<biz>/shipping-profiles/<id>/default -> make_default
    POST   /api/business-os/store/<biz>/shipping-profiles/<id>/archive -> archive_profile
    GET    /api/business-os/store/<biz>/return-policy               -> get_return_policy
    PUT    /api/business-os/store/<biz>/return-policy               -> put_return_policy

Route-pack init must call ``policies.ensure_schema()`` once (same shape as
every other pack).
"""

from __future__ import annotations

from typing import Any, Optional, Tuple

from services.business_os.store import policies as pol
from services.business_os.store import service as svc
from services.business_os.store.service import StoreError


PROFILE_FIELDS = {"name", "rate_type", "base_rate_cents", "per_item_rate_cents",
                  "regions", "min_delivery_days", "max_delivery_days"}
RETURN_POLICY_FIELDS = {"returns_accepted", "window_days", "restocking_fee_bps",
                        "return_shipping_paid_by", "policy_text"}


def _ok(body: dict, status: int = 200) -> Tuple[int, dict]:
    out = {"ok": True}
    out.update(body or {})
    return (status, out)


def _err(exc: StoreError) -> Tuple[int, dict]:
    return (int(getattr(exc, "http_status", 400)),
            {"ok": False, "error": str(exc),
             "code": getattr(exc, "code", "invalid")})


def _dark() -> Tuple[int, dict]:
    return (404, {"ok": False, "error": "Not found.", "code": "not_found"})


def _pick(payload: Any, allowed: set) -> dict:
    """Reject unknown keys loudly (mirrors the marketplace controller): silent
    field-dropping hides client bugs behind a 200."""
    if payload is None:
        return {}
    if not isinstance(payload, dict):
        raise StoreError("Invalid request body.", 400, "bad_body")
    unknown = set(payload) - allowed
    if unknown:
        raise StoreError(f"Unknown field(s): {sorted(unknown)}.", 400, "unknown_field")
    return dict(payload)


def _guard() -> bool:
    return svc.is_enabled()


# --- summary (the dashboard tiles' data source) ------------------------------
def get_summary(actor_user_id: Any, business_id: str) -> Tuple[int, dict]:
    if not _guard():
        return _dark()
    try:
        return _ok({"policies": pol.policies_summary(business_id, actor_user_id)})
    except StoreError as e:
        return _err(e)


# --- shipping profiles -------------------------------------------------------
def list_profiles(actor_user_id: Any, business_id: str, *,
                  include_archived: bool = False) -> Tuple[int, dict]:
    if not _guard():
        return _dark()
    try:
        return _ok({"profiles": pol.list_shipping_profiles(
            business_id, actor_user_id, include_archived=bool(include_archived))})
    except StoreError as e:
        return _err(e)


def get_profile(actor_user_id: Any, business_id: str,
                profile_id: str) -> Tuple[int, dict]:
    if not _guard():
        return _dark()
    try:
        return _ok({"profile": pol.get_shipping_profile(
            business_id, actor_user_id, profile_id)})
    except StoreError as e:
        return _err(e)


def create_profile(actor_user_id: Any, business_id: str, payload: Any, *,
                   context: Optional[dict] = None) -> Tuple[int, dict]:
    if not _guard():
        return _dark()
    try:
        profile = pol.create_shipping_profile(
            business_id, actor_user_id, _pick(payload, PROFILE_FIELDS),
            context=context)
        return _ok({"profile": profile}, status=201)
    except StoreError as e:
        return _err(e)


def update_profile(actor_user_id: Any, business_id: str, profile_id: str,
                   payload: Any, *,
                   context: Optional[dict] = None) -> Tuple[int, dict]:
    if not _guard():
        return _dark()
    try:
        profile = pol.update_shipping_profile(
            business_id, actor_user_id, profile_id,
            _pick(payload, PROFILE_FIELDS), context=context)
        return _ok({"profile": profile})
    except StoreError as e:
        return _err(e)


def make_default(actor_user_id: Any, business_id: str, profile_id: str, *,
                 context: Optional[dict] = None) -> Tuple[int, dict]:
    if not _guard():
        return _dark()
    try:
        profile = pol.set_default_shipping_profile(
            business_id, actor_user_id, profile_id, context=context)
        return _ok({"profile": profile})
    except StoreError as e:
        return _err(e)


def archive_profile(actor_user_id: Any, business_id: str, profile_id: str, *,
                    context: Optional[dict] = None) -> Tuple[int, dict]:
    if not _guard():
        return _dark()
    try:
        profile = pol.archive_shipping_profile(
            business_id, actor_user_id, profile_id, context=context)
        return _ok({"profile": profile})
    except StoreError as e:
        return _err(e)


# --- return policy -----------------------------------------------------------
def get_return_policy(actor_user_id: Any, business_id: str) -> Tuple[int, dict]:
    """``policy: null`` when none has ever been set — the client renders
    "Not set up", it does not receive a fabricated default."""
    if not _guard():
        return _dark()
    try:
        return _ok({"policy": pol.get_return_policy(business_id, actor_user_id)})
    except StoreError as e:
        return _err(e)


def put_return_policy(actor_user_id: Any, business_id: str, payload: Any, *,
                      context: Optional[dict] = None) -> Tuple[int, dict]:
    if not _guard():
        return _dark()
    try:
        policy = pol.upsert_return_policy(
            business_id, actor_user_id, _pick(payload, RETURN_POLICY_FIELDS),
            context=context)
        return _ok({"policy": policy})
    except StoreError as e:
        return _err(e)
