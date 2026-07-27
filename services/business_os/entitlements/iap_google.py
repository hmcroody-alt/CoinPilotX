"""Google Play Real-Time Developer Notifications (RTDN) — decode + projection.

Unlike Apple (whose signed payload is self-contained and cryptographically
verifiable offline), a Google RTDN is **not** proof of anything on its own. The
Pub/Sub message only tells you "*something* changed for this purchaseToken"; the
authoritative state must be fetched from the **Play Developer API**
(``purchases.subscriptionsv2.get``). That API call is an authenticated,
provider-side network request — out of scope for this environment and never
performed here.

So this module draws a clean **verifier boundary**: everything up to the API call
(Pub/Sub envelope decode, notification-type mapping, subject/plan resolution,
entitlement projection) is canonical and fully testable; the API call itself is an
**injected callable** (``purchase_verifier``). In production you pass a verifier
that calls the Play Developer API with a service-account token; in tests you pass a
stub returning a canned verified purchase. If the verifier returns ``None`` (or the
purchase isn't in a state that confers access) we **grant nothing** — we refuse to
project entitlements from an unverified RTDN, exactly the rule the old stub
enforced by raising.
"""

from __future__ import annotations

import base64
import json
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Optional

from services import db
from services.business_os.entitlements import providers as _prov
from services.business_os.entitlements import service as _svc


class GoogleRTDNError(ValueError):
    """Raised when an RTDN envelope is malformed. Not the same as 'unverified':
    a well-formed RTDN whose purchase can't be verified simply grants nothing."""


# Google subscriptionNotification type codes -> lifecycle intent.
# https://developer.android.com/google/play/billing/rtdn-reference
_TYPE_NAMES = {
    1: "SUBSCRIPTION_RECOVERED", 2: "SUBSCRIPTION_RENEWED",
    3: "SUBSCRIPTION_CANCELED", 4: "SUBSCRIPTION_PURCHASED",
    5: "SUBSCRIPTION_ON_HOLD", 6: "SUBSCRIPTION_IN_GRACE_PERIOD",
    7: "SUBSCRIPTION_RESTARTED", 8: "SUBSCRIPTION_PRICE_CHANGE_CONFIRMED",
    9: "SUBSCRIPTION_DEFERRED", 10: "SUBSCRIPTION_PAUSED",
    11: "SUBSCRIPTION_PAUSE_SCHEDULE_CHANGED", 12: "SUBSCRIPTION_REVOKED",
    13: "SUBSCRIPTION_EXPIRED",
}
_GRANT_TYPES = {1, 2, 4, 7}          # recovered / renewed / purchased / restarted
_GRACE_TYPES = {6, 5}               # grace period / on hold (retry) -> keep, flag
_REVOKE_TYPES = {12}                # revoked (refund/chargeback) -> strip now
_EXPIRE_TYPES = {3, 9, 10, 13}      # canceled/deferred/paused/expired -> lapse

# Google productId (base plan / subscription id) -> canonical plan_key.
GOOGLE_PRODUCT_TO_PLAN: dict[str, str] = {
    "pulsesoc_premium_monthly": "pulse_premium_monthly",
    "pulsesoc_premium_annual": "pulse_premium_annual",
    "pulsesoc_business_monthly": "pulse_business_monthly",
}


def _ms_to_iso(value: Any) -> Optional[str]:
    if value in (None, ""):
        return None
    try:
        return datetime.fromtimestamp(int(value) / 1000.0, tz=timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%S.%fZ")
    except (ValueError, TypeError, OSError):
        return None


def decode_rtdn(envelope: Mapping[str, Any]) -> dict:
    """Decode a Pub/Sub push envelope into the RTDN's subscriptionNotification.

    Envelope shape: ``{"message": {"data": <base64 JSON>, ...}, ...}``. Returns
    ``{packageName, eventTimeMillis, notificationType, notificationName,
    subscriptionId, purchaseToken}``. Raises ``GoogleRTDNError`` on malformed
    input. Pure/testable — no network, no DB.
    """
    if not isinstance(envelope, Mapping):
        raise GoogleRTDNError("RTDN envelope must be a mapping")
    msg = envelope.get("message")
    if not isinstance(msg, Mapping) or "data" not in msg:
        raise GoogleRTDNError("RTDN envelope missing message.data")
    try:
        raw = base64.b64decode(msg["data"])
        body = json.loads(raw.decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise GoogleRTDNError(f"RTDN data is not base64 JSON: {exc}") from exc

    sub = body.get("subscriptionNotification")
    if not isinstance(sub, Mapping):
        # Could be a voidedPurchase or test notification; surface it, don't crash.
        return {"packageName": body.get("packageName"),
                "eventTimeMillis": body.get("eventTimeMillis"),
                "notificationType": None, "notificationName": None,
                "subscriptionId": None, "purchaseToken": None,
                "testNotification": "testNotification" in body}
    ntype = sub.get("notificationType")
    return {
        "packageName": body.get("packageName"),
        "eventTimeMillis": body.get("eventTimeMillis"),
        "notificationType": ntype,
        "notificationName": _TYPE_NAMES.get(ntype),
        "subscriptionId": sub.get("subscriptionId"),
        "purchaseToken": sub.get("purchaseToken"),
    }


# A purchase_verifier takes (package_name, subscription_id, purchase_token) and
# returns the AUTHORITATIVE verified purchase, or None if it can't be verified.
# Expected verified shape (subset of Play subscriptionsv2 resource):
#   {"subscriptionState": "SUBSCRIPTION_STATE_ACTIVE"|...,
#    "productId": "<base plan id>", "expiryTimeMillis": <int>,
#    "externalAccountId": "<our user id>"}
PurchaseVerifier = Callable[[Optional[str], Optional[str], Optional[str]],
                            Optional[Mapping[str, Any]]]

_ACCESS_STATES = {"SUBSCRIPTION_STATE_ACTIVE", "SUBSCRIPTION_STATE_IN_GRACE_PERIOD",
                  "SUBSCRIPTION_STATE_ON_HOLD", "SUBSCRIPTION_STATE_CANCELED"}


def apply_rtdn(envelope: Mapping[str, Any], *, purchase_verifier: PurchaseVerifier,
               subject_type: str = "user", conn=None) -> dict:
    """Decode an RTDN, verify the purchase via the injected verifier, land the
    provider subscription, and project the lifecycle intent into canonical grants.

    Grants nothing unless the verifier returns a purchase in an access-conferring
    state. Idempotent per purchaseToken (the provider_subscription_id).
    """
    rtdn = decode_rtdn(envelope)
    if rtdn.get("testNotification"):
        return {"ignored": True, "reason": "test notification"}
    ntype = rtdn.get("notificationType")
    token = rtdn.get("purchaseToken")
    if ntype is None or not token:
        return {"ignored": True, "reason": "no subscription notification"}

    # Authoritative state — the boundary. Never trust the RTDN alone.
    verified = purchase_verifier(rtdn.get("packageName"),
                                 rtdn.get("subscriptionId"), token)
    if verified is None:
        return {"recorded": False, "projected": False, "revoked": False,
                "reason": "purchase could not be verified; access unchanged",
                "notification_name": rtdn.get("notificationName")}

    state = verified.get("subscriptionState")
    product_id = verified.get("productId") or rtdn.get("subscriptionId")
    plan_key = GOOGLE_PRODUCT_TO_PLAN.get(product_id) if product_id else None
    subject_id = verified.get("externalAccountId") or verified.get("obfuscatedExternalAccountId")
    period_end = _ms_to_iso(verified.get("expiryTimeMillis"))

    if ntype in _REVOKE_TYPES:
        intent = "revoke"
    elif ntype in _GRACE_TYPES:
        intent = "grace"
    elif ntype in _EXPIRE_TYPES:
        intent = "expire"
    elif ntype in _GRANT_TYPES:
        intent = "grant"
    else:
        intent = "record"

    status = {
        "grant": "active", "grace": "grace_period", "revoke": "revoked",
        "expire": "expired", "record": (rtdn.get("notificationName") or "unknown").lower(),
    }[intent]

    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        _prov.upsert_provider_subscription(
            provider="google_play", provider_subscription_id=str(token),
            subject_id=str(subject_id) if subject_id else str(token),
            plan_key=plan_key, status=status, current_period_end=period_end,
            cancel_at_period_end=(intent == "expire"),
            subject_type=subject_type, raw={"rtdn": rtdn, "verified": dict(verified)},
            conn=conn,
        )
        result = {"recorded": True, "projected": False, "revoked": False,
                  "intent": intent, "notification_name": rtdn.get("notificationName"),
                  "provider_subscription_id": str(token)}

        if subject_id is None or plan_key is None:
            result["reason"] = "unresolved subject or unmapped plan; access unchanged"
            if owned:
                conn.commit()
            return result

        # Only project access when Play says the purchase actually confers it.
        if intent in ("grant", "grace") and state in _ACCESS_STATES:
            proj = _svc.sync_subscription_entitlements(
                str(subject_id), plan_key, status="active", source="google_play",
                source_reference=str(token), period_end=period_end,
                grace_until=(period_end if intent == "grace" else None),
                subject_type=subject_type, actor="google_adapter", conn=conn)
            result["projected"] = True
            result["granted_keys"] = proj["granted_keys"]
        elif intent == "revoke":
            cat = conn.execute(
                "SELECT entitlement_key FROM business_os_ent_catalog WHERE plan_key=?",
                (plan_key,)).fetchall()
            revoked = []
            for row in cat:
                ent_key = row[0] if not hasattr(row, "keys") else row["entitlement_key"]
                _svc.revoke_entitlement(
                    str(subject_id), ent_key, reason=f"google:{rtdn.get('notificationName')}",
                    subject_type=subject_type, source="google_play",
                    source_reference=str(token), actor="google_adapter", conn=conn)
                revoked.append(ent_key)
            result["revoked"] = True
            result["revoked_keys"] = revoked
        # 'expire'/'record' leave grants to lapse at period_end

        if owned:
            conn.commit()
        return result
    finally:
        if owned:
            conn.close()
