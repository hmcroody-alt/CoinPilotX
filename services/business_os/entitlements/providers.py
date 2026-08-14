"""Provider adapters — translate provider subscription state into canonical grants.

A provider adapter has two jobs:

1. **Normalize** a provider's subscription/receipt payload into a common shape
   (subject, plan_key, status, period_end) and land it in
   ``business_os_ent_provider_subs`` (deduped by ``provider_subscription_id``).
2. **Project** that normalized subscription into canonical entitlement grants via
   ``service.sync_subscription_entitlements``.

Only **Stripe** is implemented, because Stripe is the one provider whose event
semantics we can verify server-side (and it already backs the payments ledger
slice). **Apple App Store** and **Google Play** adapters are *interfaces only*:
their receipt/notification verification is NOT built, and this module refuses to
fabricate a "verified/active" result for them. Calling them raises
``ProviderNotImplemented`` with a precise message so a caller can never mistake a
stub for a working integration. This is deliberate — silently returning success
for unverified IAP would be a correctness and revenue-integrity bug.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Mapping, Optional

from services import db
from services.business_os.entitlements import service as _svc


class ProviderError(ValueError):
    """Raised when a provider payload is malformed or cannot be mapped."""


class ProviderNotImplemented(NotImplementedError):
    """Raised by adapters whose verification path is intentionally not built.

    Distinct from ``ProviderError`` so callers can tell "bad input" apart from
    "this provider isn't wired up yet". Never swallow this into a success.
    """


# Metadata keys we accept for the app-side subject id, aligned with the payments
# Stripe handler so a single Stripe object resolves the same user everywhere.
_USER_ID_KEYS = ("pulse_user_id", "user_id", "app_user_id", "client_reference_id")


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _resolve_subject(obj: Mapping[str, Any]) -> Optional[str]:
    """Extract the app-side subject id from a provider object's metadata."""
    ref = obj.get("client_reference_id")
    if ref not in (None, ""):
        return str(ref)
    meta = obj.get("metadata")
    if isinstance(meta, Mapping):
        for key in _USER_ID_KEYS:
            val = meta.get(key)
            if val not in (None, ""):
                return str(val)
    return None


# ---------------------------------------------------------------------------
# Common landing-zone upsert
# ---------------------------------------------------------------------------
def upsert_provider_subscription(*, provider: str, provider_subscription_id: str,
                                 subject_id: Any, plan_key: Optional[str],
                                 status: str, current_period_end: Optional[str],
                                 cancel_at_period_end: bool = False,
                                 subject_type: str = "user",
                                 raw: Optional[Mapping] = None, conn=None) -> dict:
    """Idempotent upsert into ``business_os_ent_provider_subs`` keyed by
    ``provider_subscription_id`` (UNIQUE). Returns the stored row."""
    owned = conn is None
    if owned:
        conn = db.connect()
    now = _now_iso()
    raw_json = json.dumps(dict(raw), sort_keys=True, default=str) if raw else None
    try:
        cur = conn.execute(
            "SELECT id FROM business_os_ent_provider_subs "
            "WHERE provider_subscription_id = ?",
            (provider_subscription_id,),
        )
        existing = cur.fetchone()
        if existing is None:
            try:
                conn.execute(
                    "INSERT INTO business_os_ent_provider_subs "
                    "(provider, provider_subscription_id, subject_type, subject_id, "
                    "plan_key, status, current_period_end, cancel_at_period_end, "
                    "raw_json, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (provider, provider_subscription_id, subject_type, str(subject_id),
                     plan_key, status, current_period_end, 1 if cancel_at_period_end else 0,
                     raw_json, now, now),
                )
            except Exception as exc:  # racing insert -> fall through to update
                if "unique" not in str(exc).lower() and "duplicate" not in str(exc).lower():
                    raise
                existing = True
        if existing is not None:
            conn.execute(
                "UPDATE business_os_ent_provider_subs SET subject_type=?, subject_id=?, "
                "plan_key=?, status=?, current_period_end=?, cancel_at_period_end=?, "
                "raw_json=?, updated_at=? WHERE provider_subscription_id=?",
                (subject_type, str(subject_id), plan_key, status, current_period_end,
                 1 if cancel_at_period_end else 0, raw_json, now, provider_subscription_id),
            )
        if owned:
            conn.commit()
        row = conn.execute(
            "SELECT * FROM business_os_ent_provider_subs "
            "WHERE provider_subscription_id = ?",
            (provider_subscription_id,),
        ).fetchone()
        return _svc._row_to_dict(row) or {}
    finally:
        if owned:
            conn.close()


# ---------------------------------------------------------------------------
# Stripe adapter (real)
# ---------------------------------------------------------------------------
# Map a Stripe price/product id to our canonical plan_key. In production this is
# populated from Stripe dashboard price ids; kept overridable so ops can extend
# it without a code change to the projection logic.
_STRIPE_PRICE_TO_PLAN: dict[str, str] = {
    "price_premium_monthly": "pulse_premium_monthly",
    "price_premium_annual": "pulse_premium_annual",
    "price_business_monthly": "pulse_business_monthly",
}

# Stripe subscription statuses that should keep entitlement access. Cancellation
# and past_due keep access until period end (the grant's expires_at); only a hard
# terminal state stops projecting.
_STRIPE_ACTIVE_STATUSES = {"active", "trialing", "past_due"}
_STRIPE_TERMINAL_STATUSES = {"canceled", "unpaid", "incomplete_expired"}


def _epoch_to_iso(value: Any) -> Optional[str]:
    """Stripe sends period ends as unix epoch ints; convert to our ISO form."""
    if value in (None, ""):
        return None
    try:
        return datetime.fromtimestamp(int(value), tz=timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%S.%fZ")
    except (ValueError, TypeError, OSError):
        return None


def map_stripe_subscription(payload: Mapping[str, Any]) -> Optional[dict]:
    """Normalize a Stripe ``customer.subscription.*`` event (or subscription
    object) into ``{provider_subscription_id, subject_id, plan_key, status,
    current_period_end, cancel_at_period_end}``. Returns None if it isn't a
    subscription object we can map. Pure/testable — no DB writes."""
    obj = payload.get("data", {}).get("object") if "data" in payload else payload
    if not isinstance(obj, Mapping):
        return None
    sub_id = obj.get("id")
    if not sub_id:
        return None
    subject_id = _resolve_subject(obj)
    if subject_id is None:
        return None

    # Extract the price id from the first subscription item.
    plan_key = None
    price_id = None
    items = obj.get("items")
    if isinstance(items, Mapping):
        data = items.get("data")
        if isinstance(data, list) and data:
            price = data[0].get("price") if isinstance(data[0], Mapping) else None
            if isinstance(price, Mapping):
                price_id = price.get("id")
    if price_id is None:
        price_id = obj.get("plan_id") or obj.get("price_id")
    if price_id is not None:
        plan_key = _STRIPE_PRICE_TO_PLAN.get(price_id)
    # allow explicit plan_key in metadata as an override/fallback
    meta = obj.get("metadata")
    if plan_key is None and isinstance(meta, Mapping):
        plan_key = meta.get("plan_key")

    return {
        "provider_subscription_id": str(sub_id),
        "subject_id": subject_id,
        "plan_key": plan_key,
        "status": str(obj.get("status") or "unknown"),
        "current_period_end": _epoch_to_iso(obj.get("current_period_end")),
        "cancel_at_period_end": bool(obj.get("cancel_at_period_end")),
    }


def apply_stripe_subscription(payload: Mapping[str, Any], *, subject_type: str = "user",
                              conn=None) -> dict:
    """End-to-end: map a Stripe subscription payload, land it in provider_subs,
    and project it into canonical grants. Idempotent per subscription id.

    Terminal statuses (canceled/unpaid) do NOT immediately strip access — the
    grant's ``expires_at`` (period end) governs, matching the payments/report
    rule. Access ends when the clock runs out or an explicit refund revocation
    lands."""
    mapped = map_stripe_subscription(payload)
    if mapped is None:
        return {"ignored": True, "reason": "not a mappable subscription object"}
    if mapped["plan_key"] is None:
        # We recorded the sub but can't project entitlements without a plan.
        upsert_provider_subscription(
            provider="stripe", subject_type=subject_type, raw=payload,
            **{k: mapped[k] for k in
               ("provider_subscription_id", "subject_id", "plan_key",
                "status", "current_period_end", "cancel_at_period_end")},
            conn=conn,
        )
        return {"recorded": True, "projected": False,
                "reason": "unmapped plan; grants not projected",
                "provider_subscription_id": mapped["provider_subscription_id"]}

    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        upsert_provider_subscription(
            provider="stripe", subject_type=subject_type, raw=payload,
            **{k: mapped[k] for k in
               ("provider_subscription_id", "subject_id", "plan_key",
                "status", "current_period_end", "cancel_at_period_end")},
            conn=conn,
        )
        result = {"recorded": True, "projected": False,
                  "provider_subscription_id": mapped["provider_subscription_id"],
                  "status": mapped["status"]}
        if mapped["status"] in _STRIPE_ACTIVE_STATUSES or (
                mapped["status"] in _STRIPE_TERMINAL_STATUSES
                and mapped["current_period_end"]):
            proj = _svc.sync_subscription_entitlements(
                mapped["subject_id"], mapped["plan_key"],
                status="active" if mapped["status"] in _STRIPE_ACTIVE_STATUSES else mapped["status"],
                source="stripe",
                source_reference=mapped["provider_subscription_id"],
                period_end=mapped["current_period_end"],
                subject_type=subject_type, actor="stripe_adapter", conn=conn,
            )
            result["projected"] = True
            result["granted_keys"] = proj["granted_keys"]
        if owned:
            conn.commit()
        return result
    finally:
        if owned:
            conn.close()


# ---------------------------------------------------------------------------
# Apple App Store / Google Play
# ---------------------------------------------------------------------------
# The ``AppleAppStoreAdapter`` and ``GooglePlayAdapter`` stubs that used to live
# here were REMOVED. Both were interface placeholders whose every method raised
# ``ProviderNotImplemented``, written before store verification existed.
#
# Real, verifying implementations now live in sibling modules:
#
#   * ``iap_apple.py``  — StoreKit 2 / App Store Server Notifications v2 JWS
#     verification (x5c chain validation, certificate fingerprint pinning,
#     validity window, bundle id + appAccountToken binding, sandbox gating),
#     landing through ``upsert_provider_subscription`` +
#     ``service.sync_subscription_entitlements``.
#   * ``iap_google.py`` — Google Play RTDN decoding and projection.
#
# Keeping the stubs alongside the real modules was a live hazard: two importable
# classes named for the same providers, one of which silently refuses every
# purchase. A caller that reached for the obvious-looking ``AppleAppStoreAdapter``
# would have had all Apple entitlements rejected with an exception, while the
# working path sat one module over. Deleting them makes the verifying
# implementation the only thing you can import.
