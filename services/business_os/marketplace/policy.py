"""Versioned, server-authoritative PulseSoc Marketplace commercial policy."""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
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
SETTLEMENT_HOLD_HOURS_ENV_VAR = "MARKETPLACE_SETTLEMENT_HOLD_HOURS"

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


def _parse_iso(value: Any) -> datetime | None:
    """Lenient ISO-8601 parse to an aware UTC datetime, or None.

    Timestamps in this codebase are stored as TEXT (both SQLite and Postgres), are
    written by several modules, and are not uniformly suffixed, so a naive value is
    read as UTC rather than rejected.
    """
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def return_window_anchor(*, delivered_at: Any = None,
                         purchased_at: Any = None) -> datetime | None:
    """When the buyer's return window starts counting.

    Delivery is the right anchor — a buyer cannot judge an item they do not have
    yet — so it wins whenever the system recorded one. Purchase is the fallback,
    used for an order that was never marked delivered.

    The fallback is deliberately *not* "the window never opens". Anchoring on a
    delivery that may never be recorded would leave the return window open
    forever, which is the unbounded liability this function exists to close.
    """
    return _parse_iso(delivered_at) or _parse_iso(purchased_at)


def return_window_closes_at(*, delivered_at: Any = None,
                            purchased_at: Any = None) -> str | None:
    """The deadline itself, ISO-8601 UTC, or None when no anchor is known.

    Returned to buyers so the deadline is something they are told rather than
    something they discover by being refused.
    """
    anchor = return_window_anchor(delivered_at=delivered_at, purchased_at=purchased_at)
    if anchor is None:
        return None
    return (anchor + timedelta(days=STANDARD_RETURN_WINDOW_DAYS)).strftime(
        "%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def return_window_open(*, delivered_at: Any = None, purchased_at: Any = None,
                       now: datetime | None = None) -> bool:
    """May a return still be opened on this order?

    ``now`` is injectable so the boundary is testable without sleeping.

    An order with no usable timestamp at all returns True. That is the one
    fail-open case here and it is chosen on purpose: refusing a return because a
    row is missing a date would deny a buyer a real right over a bookkeeping
    defect, and the defect is the thing to fix.
    """
    anchor = return_window_anchor(delivered_at=delivered_at, purchased_at=purchased_at)
    if anchor is None:
        return True
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment <= anchor + timedelta(days=STANDARD_RETURN_WINDOW_DAYS)


def settlement_hold_hours() -> int:
    """How long a confirmed delivery waits before the seller's money is released.

    Configurable because the right answer is a business decision, not a constant:
    it moves with dispute rates, category risk and whatever a card network asks
    for next. `STANDARD_PAYOUT_PROTECTION_DAYS` remains the documented default so
    an unconfigured deployment behaves exactly as it did before this was a knob.

    A bad value falls back to that default rather than to zero. This is the
    opposite of how the fulfillment timeouts fail, and deliberately so: there,
    doing nothing leaves an order sitting where it is, while here doing nothing
    would mean releasing the money the instant delivery is confirmed — a typo in
    a deployment variable must not be able to pay a seller early.

    An explicit zero is honoured. Waiving the hold is a decision the owner is
    entitled to make, and silently overriding it would make the variable a lie.
    """
    raw = str(os.getenv(SETTLEMENT_HOLD_HOURS_ENV_VAR) or "").strip()
    if not raw:
        return STANDARD_PAYOUT_PROTECTION_DAYS * 24
    try:
        hours = int(raw)
    except ValueError:
        return STANDARD_PAYOUT_PROTECTION_DAYS * 24
    if hours < 0:
        return STANDARD_PAYOUT_PROTECTION_DAYS * 24
    return hours


def settlement_hold_ends_at(delivered_at: datetime) -> datetime:
    """The moment a delivery confirmed at ``delivered_at`` clears its hold."""
    return delivered_at + timedelta(hours=settlement_hold_hours())


def fee_policy_active() -> bool:
    """Activation requires all three owner gates; unset is safely inactive."""
    return all((
        _enabled("MARKETPLACE_STANDARD_V1_OWNER_APPROVED"),
        _enabled("MARKETPLACE_STANDARD_V1_SELLER_DISCLOSURE_READY"),
        _effective_now(str(os.getenv("MARKETPLACE_STANDARD_V1_EFFECTIVE_AT") or "")),
    ))


def platform_fee_bps() -> int:
    """The one commission rate any Marketplace checkout may charge.

    A commission has to be the rate the seller was actually shown, so it cannot
    come from a database row an admin can edit out from under a live checkout.
    Zero until the owner opens all three gates, and `PROPOSED_PLATFORM_FEE_BPS`
    after — there is no third value.
    """
    return PROPOSED_PLATFORM_FEE_BPS if fee_policy_active() else 0


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

