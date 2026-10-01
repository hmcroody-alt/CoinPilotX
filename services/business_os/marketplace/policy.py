"""Versioned, server-authoritative PulseSoc Marketplace commercial policy."""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any


POLICY_VERSION = "MARKETPLACE_STANDARD_V1"
FEE_BASE = "merchandise_net_after_seller_discount"
RETURN_POLICY_VERSION = "MARKETPLACE_RETURNS_V1"
LISTING_POLICY_VERSION = "MARKETPLACE_LISTINGS_V1"
PAYOUT_POLICY_VERSION = "MARKETPLACE_PAYOUTS_V1"
BUYER_PROTECTION_VERSION = "MARKETPLACE_BUYER_PROTECTION_V1"
SELLER_TERMS_VERSION = "MARKETPLACE_SELLER_TERMS_V1"
PROHIBITED_GOODS_VERSION = "MARKETPLACE_GOODS_V1"

PROPOSED_PLATFORM_FEE_BPS = 500
BUYER_SERVICE_FEE_CENTS = 0
LISTING_FEE_CENTS = 0
STANDARD_MONTHLY_SELLER_FEE_CENTS = 0
INVENTORY_RESERVATION_TTL_SECONDS = 15 * 60
OFFER_PRICE_LOCK_SECONDS = 24 * 60 * 60
STANDARD_RETURN_WINDOW_DAYS = 14
STANDARD_PAYOUT_PROTECTION_DAYS = 2

PROHIBITED_CATEGORY_KEYS = frozenset({
    "illegal_goods", "stolen_goods", "counterfeit_goods", "weapons",
    "explosives", "illegal_drugs", "controlled_substances", "prescription_drugs",
    "tobacco_nicotine", "alcohol", "human_body_parts", "hazardous_materials",
    "recalled_products", "wildlife_trafficking", "sexual_exploitation",
    "personal_data_credentials", "malware", "surveillance_abuse",
    "extremist_merchandise", "financial_fraud_tools", "gambling",
    "age_restricted_goods",
})
RESTRICTED_CATEGORY_KEYS = frozenset({
    "high_value_collectibles", "luxury_goods", "medical_devices",
    "authenticity_review", "regulated_goods",
})
LEGAL_COMPLIANCE_REVIEW_REQUIRED = (
    "sales_tax", "marketplace_facilitator", "consumer_protection",
    "high_volume_seller", "inform_act", "restricted_goods", "tax_reporting",
)


class MarketplacePolicyError(ValueError):
    pass


def _enabled(name: str) -> bool:
    return str(os.getenv(name) or "").strip().lower() in {"1", "true", "yes", "on", "enabled"}


def _effective_now(value: str) -> bool:
    if not value:
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed <= datetime.now(timezone.utc)
    except ValueError:
        return False


def fee_policy_active() -> bool:
    """Activation requires all three owner gates; unset is safely inactive."""
    return all((
        _enabled("MARKETPLACE_STANDARD_V1_OWNER_APPROVED"),
        _enabled("MARKETPLACE_STANDARD_V1_SELLER_DISCLOSURE_READY"),
        _effective_now(str(os.getenv("MARKETPLACE_STANDARD_V1_EFFECTIVE_AT") or "")),
    ))


@dataclass(frozen=True)
class MarketplaceQuote:
    currency: str
    merchandise_gross_cents: int
    seller_discount_cents: int
    merchandise_net_cents: int
    shipping_cents: int
    tax_cents: int
    buyer_service_fee_cents: int
    buyer_total_cents: int
    platform_fee_bps: int
    platform_fee_cents: int
    seller_shipping_credit_cents: int
    seller_earnings_cents: int
    fee_policy_version: str
    fee_base: str
    fee_policy_active: bool
    return_policy_version: str
    listing_policy_version: str
    payout_policy_version: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _money(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise MarketplacePolicyError(f"{name} must be a non-negative integer in minor units.")
    return value


def quote(*, unit_price_cents: int, quantity: int = 1, seller_discount_cents: int = 0,
          shipping_cents: int = 0, tax_cents: int = 0,
          seller_shipping_credit_cents: int | None = None, currency: str = "usd",
          activate_proposed_policy: bool | None = None) -> MarketplaceQuote:
    unit = _money(unit_price_cents, "unit_price_cents")
    if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity <= 0:
        raise MarketplacePolicyError("quantity must be a positive integer.")
    discount = _money(seller_discount_cents, "seller_discount_cents")
    shipping = _money(shipping_cents, "shipping_cents")
    tax = _money(tax_cents, "tax_cents")
    merchandise_gross = unit * quantity
    if discount > merchandise_gross:
        raise MarketplacePolicyError("seller discount cannot exceed merchandise gross.")
    merchandise_net = merchandise_gross - discount
    shipping_credit = shipping if seller_shipping_credit_cents is None else _money(
        seller_shipping_credit_cents, "seller_shipping_credit_cents")
    active = fee_policy_active() if activate_proposed_policy is None else bool(activate_proposed_policy)
    fee_bps = PROPOSED_PLATFORM_FEE_BPS if active else 0
    platform_fee = (merchandise_net * fee_bps) // 10_000
    buyer_total = merchandise_net + shipping + tax + BUYER_SERVICE_FEE_CENTS
    seller_earnings = merchandise_net + shipping_credit - platform_fee
    return MarketplaceQuote(
        currency=str(currency or "usd").lower(),
        merchandise_gross_cents=merchandise_gross,
        seller_discount_cents=discount,
        merchandise_net_cents=merchandise_net,
        shipping_cents=shipping,
        tax_cents=tax,
        buyer_service_fee_cents=BUYER_SERVICE_FEE_CENTS,
        buyer_total_cents=buyer_total,
        platform_fee_bps=fee_bps,
        platform_fee_cents=platform_fee,
        seller_shipping_credit_cents=shipping_credit,
        seller_earnings_cents=seller_earnings,
        fee_policy_version=POLICY_VERSION,
        fee_base=FEE_BASE,
        fee_policy_active=active,
        return_policy_version=RETURN_POLICY_VERSION,
        listing_policy_version=LISTING_POLICY_VERSION,
        payout_policy_version=PAYOUT_POLICY_VERSION,
    )


def platform_fee_reversal(*, original_merchandise_net_cents: int,
                          original_platform_fee_cents: int,
                          refunded_merchandise_cents: int) -> int:
    base = _money(original_merchandise_net_cents, "original_merchandise_net_cents")
    fee = _money(original_platform_fee_cents, "original_platform_fee_cents")
    refunded = _money(refunded_merchandise_cents, "refunded_merchandise_cents")
    if refunded > base:
        raise MarketplacePolicyError("refunded merchandise cannot exceed the original merchandise net.")
    if not base or not fee or not refunded:
        return 0
    if refunded == base:
        return fee
    return min(fee, (fee * refunded) // base)


def listing_category_decision(category_key: str) -> str:
    key = str(category_key or "").strip().lower()
    if key in PROHIBITED_CATEGORY_KEYS:
        return "PROHIBITED"
    if key in RESTRICTED_CATEGORY_KEYS:
        return "MANUAL_REVIEW_REQUIRED"
    return "ALLOWED"

