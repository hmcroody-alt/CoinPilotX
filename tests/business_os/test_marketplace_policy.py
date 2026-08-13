import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from services.business_os.marketplace import policy


def test_launch_examples_use_integer_minor_units():
    order_a = policy.quote(
        unit_price_cents=10_000, shipping_cents=1_000, tax_cents=800,
        activate_proposed_policy=True,
    )
    assert order_a.buyer_total_cents == 11_800
    assert order_a.platform_fee_cents == 500
    assert order_a.seller_earnings_cents == 10_500
    assert order_a.buyer_service_fee_cents == 0

    order_b = policy.quote(
        unit_price_cents=10_000, seller_discount_cents=2_000,
        shipping_cents=1_000, tax_cents=700, activate_proposed_policy=True,
    )
    assert order_b.merchandise_net_cents == 8_000
    assert order_b.platform_fee_cents == 400
    assert order_b.buyer_total_cents == 9_700


def test_tax_and_shipping_are_excluded_from_commission():
    quote = policy.quote(
        unit_price_cents=10_000, shipping_cents=10_000, tax_cents=10_000,
        activate_proposed_policy=True,
    )
    assert quote.platform_fee_cents == 500


def test_fee_is_inactive_without_owner_gates(monkeypatch):
    for key in (
        "MARKETPLACE_STANDARD_V1_OWNER_APPROVED",
        "MARKETPLACE_STANDARD_V1_SELLER_DISCLOSURE_READY",
        "MARKETPLACE_STANDARD_V1_EFFECTIVE_AT",
    ):
        monkeypatch.delenv(key, raising=False)
    quote = policy.quote(unit_price_cents=10_000)
    assert quote.fee_policy_active is False
    assert quote.platform_fee_cents == 0
    assert quote.fee_policy_version == "MARKETPLACE_STANDARD_V1"


def test_partial_and_full_fee_reversal():
    assert policy.platform_fee_reversal(
        original_merchandise_net_cents=10_000,
        original_platform_fee_cents=500,
        refunded_merchandise_cents=4_000,
    ) == 200
    assert policy.platform_fee_reversal(
        original_merchandise_net_cents=10_000,
        original_platform_fee_cents=500,
        refunded_merchandise_cents=10_000,
    ) == 500


def test_goods_catalog_defaults_safe():
    assert policy.listing_category_decision("counterfeit_goods") == "PROHIBITED"
    assert policy.listing_category_decision("luxury_goods") == "MANUAL_REVIEW_REQUIRED"
    assert policy.listing_category_decision("home_goods") == "ALLOWED"
