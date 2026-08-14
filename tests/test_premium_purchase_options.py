"""Policy tests for premium_purchase_options() — the dual-provider resolver.

These assert the honest, App-Review-safe behaviour: StoreKit is the required
rail on iOS, Stripe is NOT offered on iOS without an approved external-purchase
entitlement for the region, off-Apple platforms use Stripe, and a member with an
existing active subscription is never sold a second one on the other provider.
"""

from services.business_os.entitlements.purchase_options import (
    premium_purchase_options,
    PROVIDER_APPLE_IAP,
    PROVIDER_STRIPE,
)


def test_ios_offers_storekit_and_blocks_stripe_by_default():
    opts = premium_purchase_options(platform="ios")
    assert opts["storekit"]["available"] is True
    assert opts["storekit"]["reason"] == "storekit_ios_iap_required"
    assert opts["storekit"]["product_ids"]["monthly"] == "com.pulsesoc.premium.monthly"
    assert opts["storekit"]["product_ids"]["annual"] == "com.pulsesoc.premium.annual"
    assert opts["stripe"]["available"] is False
    assert opts["stripe"]["reason"] == "stripe_blocked_ios_guideline_3_1_1"


def test_ipados_treated_as_apple_platform():
    opts = premium_purchase_options(platform="ipados")
    assert opts["storekit"]["available"] is True
    assert opts["stripe"]["available"] is False


def test_web_offers_stripe_not_storekit():
    opts = premium_purchase_options(platform="web")
    assert opts["storekit"]["available"] is False
    assert opts["storekit"]["reason"] == "storekit_unavailable_off_apple_platform"
    assert opts["stripe"]["available"] is True
    assert opts["stripe"]["reason"] == "stripe_default_off_apple_platform"
    assert opts["stripe"]["plans"]["annual"] == "price_premium_annual"


def test_android_offers_stripe_not_storekit():
    opts = premium_purchase_options(platform="android")
    assert opts["storekit"]["available"] is False
    assert opts["stripe"]["available"] is True


def test_unknown_platform_offers_nothing():
    opts = premium_purchase_options(platform="playstation")
    assert opts["storekit"]["available"] is False
    assert opts["stripe"]["available"] is False
    assert opts["reason_codes"] == ["unknown_platform"]


def test_ios_external_entitlement_requires_covered_region():
    covered = frozenset({"NL", "KR"})
    # No region given -> fail closed even with entitlement present.
    opts = premium_purchase_options(
        platform="ios",
        ios_external_purchase_entitlement=True,
        external_purchase_regions=covered,
    )
    assert opts["stripe"]["available"] is False
    assert opts["stripe"]["reason"] == "stripe_blocked_ios_region_not_covered"

    # Region outside the covered set -> still blocked.
    opts_us = premium_purchase_options(
        platform="ios",
        region="US",
        ios_external_purchase_entitlement=True,
        external_purchase_regions=covered,
    )
    assert opts_us["stripe"]["available"] is False
    assert opts_us["stripe"]["reason"] == "stripe_blocked_ios_region_not_covered"

    # Region inside the covered set -> Stripe opens, StoreKit still offered too.
    opts_nl = premium_purchase_options(
        platform="ios",
        region="nl",
        ios_external_purchase_entitlement=True,
        external_purchase_regions=covered,
    )
    assert opts_nl["stripe"]["available"] is True
    assert opts_nl["stripe"]["reason"] == "stripe_allowed_ios_external_entitlement_region"
    assert opts_nl["storekit"]["available"] is True


def test_ios_entitlement_without_region_scope_fails_closed():
    opts = premium_purchase_options(
        platform="ios",
        region="NL",
        ios_external_purchase_entitlement=True,
        external_purchase_regions=None,
    )
    assert opts["stripe"]["available"] is False
    assert opts["stripe"]["reason"] == "stripe_blocked_ios_region_not_covered"


def test_existing_apple_subscription_suppresses_stripe():
    opts = premium_purchase_options(
        platform="web",
        existing_active_provider=PROVIDER_APPLE_IAP,
    )
    assert opts["stripe"]["available"] is False
    assert opts["stripe"]["reason"] == "stripe_suppressed_existing_apple"


def test_existing_stripe_subscription_suppresses_storekit():
    opts = premium_purchase_options(
        platform="ios",
        existing_active_provider=PROVIDER_STRIPE,
    )
    assert opts["storekit"]["available"] is False
    assert opts["storekit"]["reason"] == "storekit_suppressed_existing_stripe"
    # And Stripe is still not opened on iOS without an entitlement.
    assert opts["stripe"]["available"] is False


def test_reason_codes_collect_both_rail_reasons():
    opts = premium_purchase_options(platform="ios")
    assert "storekit_ios_iap_required" in opts["reason_codes"]
    assert "stripe_blocked_ios_guideline_3_1_1" in opts["reason_codes"]
