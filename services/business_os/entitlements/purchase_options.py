"""Server-authoritative Premium purchase-option resolver.

The client reports *facts* (platform, storefront region) and the server returns
which payment rails it may offer for a Premium subscription, each with an
explicit machine reason code. The screen renders what it is given; it never
decides on its own that Stripe is allowed on iOS, and it never hides StoreKit.

Why this is not a client switch
-------------------------------
Offering Stripe on iOS for a digital subscription is an App Review Guideline
3.1.1 violation *unless* the build actually carries an Apple-approved external
purchase entitlement valid for the member's storefront. That is a fact only the
server knows (it depends on the signed build and Apple's approval), so a
"universal iOS Stripe" toggle in the app would be both a policy risk and a lie.
This resolver therefore:

  * always offers StoreKit on Apple platforms (the required rail), and
  * offers Stripe on iOS ONLY when an explicit, server-known external-purchase
    entitlement covers the region — otherwise it is refused with a reason code.

Cross-provider de-duplication
-----------------------------
A member who already holds an *active* subscription on one provider must not be
sold a second subscription on the other — that is how people end up paying
Apple and Stripe simultaneously for the same membership. When
``existing_active_provider`` is set, the *other* provider's purchase option is
suppressed with a ``*_suppressed_existing_*`` reason code. Management of the
existing subscription is a separate concern (provider-specific manage URLs);
this resolver only governs *new* purchases.

Pure by construction
--------------------
No database, no network, no clock. Everything is derived from the arguments so
the policy is unit-testable in isolation and cannot drift with I/O. Callers pass
in the server-known facts (region, entitlement, existing provider); this module
turns them into an offer.
"""

from __future__ import annotations

from typing import Optional

PROVIDER_APPLE_IAP = "apple_iap"
PROVIDER_STRIPE = "stripe"

_IOS_PLATFORMS = {"ios", "ipados"}
_VALID_PLATFORMS = _IOS_PLATFORMS | {"android", "web"}

# The two canonical Premium products, mirrored from pulse_payment_router. The
# resolver reports the product ids for the StoreKit rail so the client does not
# hardcode them; prices are NOT included here — Apple owns the price per
# storefront and Stripe owns its own, and neither is a fact this module may state.
_STOREKIT_PRODUCTS = {
    "monthly": "com.pulsesoc.premium.monthly",
    "annual": "com.pulsesoc.premium.annual",
}

# Stripe price handles, resolved by the checkout service into live Price ids.
_STRIPE_PLANS = {
    "monthly": "price_premium_monthly",
    "annual": "price_premium_annual",
}


def _norm_region(region: Optional[str]) -> Optional[str]:
    if not region:
        return None
    return str(region).strip().upper() or None


def premium_purchase_options(
    *,
    platform: str,
    region: Optional[str] = None,
    existing_active_provider: Optional[str] = None,
    ios_external_purchase_entitlement: bool = False,
    external_purchase_regions: Optional[frozenset] = None,
) -> dict:
    """Resolve the Premium purchase rails available to a subject.

    Args:
        platform: client platform fact — ``ios``/``ipados``/``android``/``web``.
        region: ISO-3166 storefront country the member is buying from, if known.
        existing_active_provider: the provider of the member's CURRENT active
            Premium subscription, or ``None``. Drives cross-provider de-dup.
        ios_external_purchase_entitlement: whether THIS build carries an
            Apple-approved external purchase entitlement. Server-known fact.
        external_purchase_regions: regions the entitlement is approved for. When
            ``None`` and the entitlement is present, no region is treated as
            covered (fail-closed): an entitlement with an unknown scope must not
            silently open Stripe everywhere on iOS.

    Returns:
        ``{storekit, stripe, reason_codes}`` where each rail is
        ``{available: bool, reason: str, product_ids|plans: {...}}``.
    """
    platform = str(platform or "").strip().lower()
    region = _norm_region(region)
    existing = str(existing_active_provider or "").strip().lower() or None
    reason_codes: list[str] = []

    def rail(available: bool, reason: str, extra: dict) -> dict:
        reason_codes.append(reason)
        return {"available": available, "reason": reason, **extra}

    if platform not in _VALID_PLATFORMS:
        return {
            "storekit": {"available": False, "reason": "unknown_platform", "product_ids": {}},
            "stripe": {"available": False, "reason": "unknown_platform", "plans": {}},
            "reason_codes": ["unknown_platform"],
        }

    is_apple = platform in _IOS_PLATFORMS

    # --- StoreKit rail ------------------------------------------------------
    if not is_apple:
        storekit = rail(False, "storekit_unavailable_off_apple_platform", {"product_ids": {}})
    elif existing == PROVIDER_STRIPE:
        # Already paying via Stripe — do not sell a second Apple subscription.
        storekit = rail(False, "storekit_suppressed_existing_stripe", {"product_ids": {}})
    else:
        storekit = rail(True, "storekit_ios_iap_required", {"product_ids": dict(_STOREKIT_PRODUCTS)})

    # --- Stripe rail --------------------------------------------------------
    if existing == PROVIDER_APPLE_IAP:
        # Already paying via Apple — do not sell a second Stripe subscription.
        stripe = rail(False, "stripe_suppressed_existing_apple", {"plans": {}})
    elif not is_apple:
        stripe = rail(True, "stripe_default_off_apple_platform", {"plans": dict(_STRIPE_PLANS)})
    elif not ios_external_purchase_entitlement:
        # iOS digital subscription with no external-purchase entitlement: IAP only.
        stripe = rail(False, "stripe_blocked_ios_guideline_3_1_1", {"plans": {}})
    else:
        covered = external_purchase_regions or frozenset()
        if region is not None and region in covered:
            stripe = rail(True, "stripe_allowed_ios_external_entitlement_region", {"plans": dict(_STRIPE_PLANS)})
        else:
            # Entitlement exists but not for this (or an unknown) region: fail closed.
            stripe = rail(False, "stripe_blocked_ios_region_not_covered", {"plans": {}})

    return {"storekit": storekit, "stripe": stripe, "reason_codes": reason_codes}
