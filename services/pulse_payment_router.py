"""PulseSoc unified payment router — the server decides the provider.

The client reports *facts* (platform, what is being bought); this module applies
*policy* and returns the provider the client must use. The client never chooses:
a purchase request naming a provider is ignored in favour of this decision, so a
tampered client cannot route an Apple-guideline-3.1.1 digital purchase around
StoreKit, and cannot push a physical-goods purchase into IAP (guideline 3.1.3(e)
requires those to NOT use IAP).

Policy basis (verified against the current App Review Guidelines during the
payments mission, 2026-08):
  * 3.1.1  — digital goods/services consumed inside the app on iOS must use IAP.
    Advertising credits displayed and spent in-app are digital goods.
  * 3.1.3(e) — goods/services consumed outside the app (physical goods,
    real-world services) must use purchase methods other than IAP.
  * 3.1.5(b)/Connect — creator payouts move through Stripe Connect, never IAP.

Classification is explicit and enumerated. Anything not enumerated is
``ambiguous`` and is REFUSED with a flag rather than guessed — a wrong guess in
either direction is an App Review rejection or a 30% commission leak.
"""

from __future__ import annotations

import os

# ---------------------------------------------------------------------------
# Providers
# ---------------------------------------------------------------------------
PROVIDER_APPLE_IAP = "apple_iap"
PROVIDER_STRIPE = "stripe"
PROVIDER_STRIPE_CONNECT = "stripe_connect"
PROVIDER_INTERNAL_LEDGER = "internal_ledger"

# Money provenance labels used on ledger rows. One credit, one provenance.
PROVENANCE_APPLE_IAP = "cash_apple_iap"
PROVENANCE_STRIPE = "cash_stripe"
PROVENANCE_PROMO = "promo_non_cash"

# ---------------------------------------------------------------------------
# Item classification — enumerated, never inferred
# ---------------------------------------------------------------------------
# digital: consumed inside the app -> IAP on iOS (guideline 3.1.1)
DIGITAL_ITEM_TYPES = {
    "ad_credits",            # advertiser wallet top-up (the IAP tier products)
    "premium_subscription",  # existing StoreKit subscriptions
    "business_subscription",
}
# physical / real-world: must NOT use IAP (guideline 3.1.3(e))
PHYSICAL_ITEM_TYPES = {
    "marketplace_physical",  # physical goods sold in the marketplace
    "real_world_service",    # services rendered outside the app
}
# payouts are not purchases at all
PAYOUT_ITEM_TYPES = {"creator_payout", "seller_payout"}
# promo credits are minted internally, never sold
PROMO_ITEM_TYPES = {"promo_credit_grant"}

IOS_PLATFORMS = {"ios", "ipados"}
VALID_PLATFORMS = IOS_PLATFORMS | {"android", "web"}


def classify_item(item_type: str) -> str:
    item_type = str(item_type or "").strip().lower()
    if item_type in DIGITAL_ITEM_TYPES:
        return "digital"
    if item_type in PHYSICAL_ITEM_TYPES:
        return "physical"
    if item_type in PAYOUT_ITEM_TYPES:
        return "payout"
    if item_type in PROMO_ITEM_TYPES:
        return "promo"
    return "ambiguous"


def route_payment(*, platform: str, item_type: str) -> dict:
    """Decide the provider for a purchase. Pure; no I/O.

    Returns ``{ok, provider, classification, policy_basis}`` or, for anything
    that cannot be classified, ``{ok: False, flagged: True, ...}`` — the caller
    must surface the flag, not fall back to a default provider.
    """
    platform = str(platform or "").strip().lower()
    if platform not in VALID_PLATFORMS:
        return {"ok": False, "error": "Unknown platform.", "flagged": True,
                "classification": "unknown_platform"}
    classification = classify_item(item_type)
    if classification == "ambiguous":
        # Mission rule: DO NOT guess. Refuse and flag for human classification.
        return {"ok": False, "flagged": True, "classification": "ambiguous",
                "error": "This item type has no payment classification yet.",
                "item_type": str(item_type or "")[:80]}
    if classification == "payout":
        return {"ok": True, "provider": PROVIDER_STRIPE_CONNECT,
                "classification": classification,
                "policy_basis": "payouts move via Stripe Connect, never IAP"}
    if classification == "promo":
        return {"ok": True, "provider": PROVIDER_INTERNAL_LEDGER,
                "classification": classification,
                "policy_basis": "promotional credits are non-cash internal ledger entries"}
    if classification == "digital" and platform in IOS_PLATFORMS:
        return {"ok": True, "provider": PROVIDER_APPLE_IAP,
                "classification": classification,
                "policy_basis": "App Review Guideline 3.1.1: in-app digital goods on iOS use IAP"}
    if classification == "physical":
        return {"ok": True, "provider": PROVIDER_STRIPE,
                "classification": classification,
                "policy_basis": "App Review Guideline 3.1.3(e): physical goods must not use IAP"}
    # digital on android/web
    return {"ok": True, "provider": PROVIDER_STRIPE,
            "classification": classification,
            "policy_basis": "digital goods off-iOS settle via Stripe"}


# ---------------------------------------------------------------------------
# Apple IAP ad-credit catalog — server-side truth for productId -> credit
# ---------------------------------------------------------------------------
# These mirror the consumables registered in App Store Connect (App 6777591572).
# The wallet credit amount ALWAYS comes from this table — never from the client
# and never from a price field inside the transaction payload.
APPLE_ADCREDIT_PRODUCTS: dict[str, int] = {
    "com.pulsesoc.adcredits.tier1": 499,
    "com.pulsesoc.adcredits.tier2": 999,
    "com.pulsesoc.adcredits.tier3": 2499,
    "com.pulsesoc.adcredits.tier4": 4999,
    "com.pulsesoc.adcredits.tier5": 9999,
}

APPLE_BUNDLE_ID = "com.pulsesoc.app"


def expected_bundle_ids() -> set[str]:
    """Production bundle id plus the dev-client id when explicitly allowed."""
    ids = {APPLE_BUNDLE_ID}
    extra = (os.getenv("APPLE_IAP_EXTRA_BUNDLE_IDS", "") or "").strip()
    for bundle in extra.split(","):
        bundle = bundle.strip()
        if bundle:
            ids.add(bundle)
    return ids


def adcredit_catalog() -> list[dict]:
    """Client-displayable catalog. Amounts are informational; the credit on
    verification is still resolved server-side from APPLE_ADCREDIT_PRODUCTS."""
    return [
        {"product_id": pid, "amount_cents": cents, "currency": "usd",
         "credit_display": f"${cents / 100:,.2f}"}
        for pid, cents in sorted(APPLE_ADCREDIT_PRODUCTS.items(),
                                 key=lambda kv: kv[1])
    ]
