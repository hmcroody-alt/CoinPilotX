"""Canonical commercial quote authority for every legacy Marketplace checkout."""

from __future__ import annotations

import hashlib
import json
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from services.business_os.marketplace import policy

QUOTE_VERSION = "MARKETPLACE_QUOTE_V1"
QUOTE_TTL_SECONDS = 15 * 60


def _money(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer in minor units")
    return value


def create_quote(*, listing_id: int, seller_id: int, store_id: Any = None,
                 variant_id: Any = None, quantity: int, unit_price_minor: int,
                 currency: str, live_fee_bps: int,
                 seller_discount_minor: int = 0, shipping_minor: int = 0,
                 tax_minor: int = 0, seller_shipping_credit_minor: int | None = None,
                 offer_id: Any = None, offer_accepted_at: Any = None,
                 offer_expires_at: Any = None, shipping: dict | None = None,
                 tax: dict | None = None, discount: dict | None = None) -> dict:
    if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity <= 0:
        raise ValueError("quantity must be a positive integer")
    rate = _money(live_fee_bps, "live_fee_bps")
    base = policy.quote(
        unit_price_cents=_money(unit_price_minor, "unit_price_minor"),
        quantity=quantity,
        seller_discount_cents=_money(seller_discount_minor, "seller_discount_minor"),
        shipping_cents=_money(shipping_minor, "shipping_minor"),
        tax_cents=_money(tax_minor, "tax_minor"),
        seller_shipping_credit_cents=seller_shipping_credit_minor,
        currency=currency,
        activate_proposed_policy=False,
    ).as_dict()
    platform_fee = (base["merchandise_net_cents"] * rate) // 10_000
    seller_earnings = base["merchandise_net_cents"] + base["seller_shipping_credit_cents"] - platform_fee
    now = datetime.now(timezone.utc)
    identity = {
        "listing_id": int(listing_id), "seller_id": int(seller_id),
        "quantity": quantity, "unit_price_minor": unit_price_minor,
        "currency": str(currency or "USD").upper(), "fee_bps": rate,
        "offer_id": offer_id,
    }
    quote_id = "mktq_" + hashlib.sha256(
        (json.dumps(identity, sort_keys=True, default=str) + secrets.token_hex(8)).encode()
    ).hexdigest()[:32]
    return {
        "quote_id": quote_id,
        "quote_version": QUOTE_VERSION,
        "listing_id": int(listing_id),
        "seller_id": int(seller_id),
        "store_id": store_id,
        "variant_id": variant_id,
        "quantity": quantity,
        "unit_price_minor": int(unit_price_minor),
        "merchandise_gross_minor": base["merchandise_gross_cents"],
        "seller_discount_minor": base["seller_discount_cents"],
        "merchandise_net_minor": base["merchandise_net_cents"],
        "shipping_minor": base["shipping_cents"],
        "tax_minor": base["tax_cents"],
        "buyer_service_fee_minor": base["buyer_service_fee_cents"],
        "buyer_total_minor": base["buyer_total_cents"],
        "platform_fee_bps": rate,
        "platform_fee_minor": platform_fee,
        "seller_shipping_credit_minor": base["seller_shipping_credit_cents"],
        "seller_earnings_minor": seller_earnings,
        "currency": str(currency or "USD").upper(),
        "fee_policy_version": "MARKETPLACE_STANDARD_V1" if policy.fee_policy_active() else "MARKETPLACE_LEGACY_CURRENT",
        "fee_base": policy.FEE_BASE,
        "return_policy_version": policy.RETURN_POLICY_VERSION,
        "payout_policy_version": policy.PAYOUT_POLICY_VERSION,
        "listing_policy_version": policy.LISTING_POLICY_VERSION,
        "offer_id": offer_id,
        "offer_price_minor": int(unit_price_minor) if offer_id else None,
        "offer_accepted_at": offer_accepted_at,
        "offer_expires_at": offer_expires_at,
        "shipping_snapshot": shipping or {},
        "tax_snapshot": tax or {"source": "not_calculated", "amount_minor": int(tax_minor)},
        "discount_snapshot": discount or {"seller_funded_minor": int(seller_discount_minor)},
        "quote_created_at": now.isoformat(),
        "quote_expires_at": (now + timedelta(seconds=QUOTE_TTL_SECONDS)).isoformat(),
    }


def transaction_metadata(existing: dict | None, quote: dict, *, payout_state: str) -> str:
    return json.dumps({**(existing or {}), "commercial_quote": quote,
                       "payout_state": payout_state}, sort_keys=True, default=str)
