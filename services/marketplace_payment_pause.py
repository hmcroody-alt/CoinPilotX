"""Marketplace card-payment policy: the global gate, and nothing else.

Stripe/card infrastructure stays in place for Marketplace and for every other
PulseSoc payment surface. This module only gates *new Marketplace checkout
starts* while cash / pickup / in-person settlement remains active.

This is the **outer** gate. Passing it means card payments are switched on for
the platform, never that a particular seller may take one. Per-seller
eligibility — Connect onboarding, `charges_enabled`, `payouts_enabled`,
outstanding requirements — is a separate authority and still has to be asked.
"""

from __future__ import annotations

import os
from typing import Any

MARKETPLACE_CARD_UNAVAILABLE_BADGE = "Temporarily Unavailable"
MARKETPLACE_CARD_UNAVAILABLE_CODE = "PAYMENT_UNAVAILABLE"
MARKETPLACE_CARD_UNAVAILABLE_MESSAGE = (
    "Marketplace card payments are temporarily unavailable. Choose cash, local pickup, "
    "or in-person payment."
)

_CASH_MODES = {
    "cash",
    "cash_on_pickup",
    "cash_pickup",
    "pickup_cash",
    "local_pickup",
    "local_pickup_cash",
    "in_person",
    "in_person_cash",
    "cash_in_person",
    "pay_in_person",
}


def normalize_marketplace_payment_mode(raw: Any) -> str:
    """Return the authoritative Marketplace payment lane for this checkout.

    Empty / legacy / Stripe-specific values are treated as card because the old
    Marketplace checkout default was Stripe. This makes the pause safe for old
    clients too: they keep seeing the preserved card path, but cannot start it.
    """

    value = str(raw or "").strip().lower().replace("-", "_").replace(" ", "_")
    return "cash" if value in _CASH_MODES else "card"


def is_marketplace_cash_payment(raw: Any) -> bool:
    return normalize_marketplace_payment_mode(raw) == "cash"


CARD_PAYMENTS_ENABLED_ENV_VAR = "MARKETPLACE_CARD_PAYMENTS_ENABLED"

_TRUTHY = {"1", "true", "yes", "on", "enabled"}


def marketplace_card_payments_enabled() -> bool:
    """Is the Marketplace card rail switched on for the platform?

    Fails closed on every input that is not an explicit, recognised yes: unset,
    blank, ``false``, and anything unparseable all mean disabled. An operator who
    fat-fingers the value gets the safe answer rather than a live card rail.

    Read per call, not at import, so the value a process acts on is the value it
    actually has — and so a test can set it without reloading the module.
    """

    return str(os.getenv(CARD_PAYMENTS_ENABLED_ENV_VAR) or "").strip().lower() in _TRUTHY


def marketplace_card_payments_paused() -> bool:
    """The global pause on Marketplace card starts.

    Kept as a function so all entry points read as a policy check instead of a
    scattered constant. Do not reuse this for Premium, ads, payouts, or other
    non-Marketplace payment rails.

    Clearing this is necessary and not sufficient. It says the rail exists; the
    seller's own Connect state decides whether this particular checkout may use
    it.
    """

    return not marketplace_card_payments_enabled()


def platform_fee_bps_for_marketplace_payment(configured_bps: int, raw_mode: Any) -> int:
    """Cash / pickup / in-person Marketplace settlements carry no platform fee."""

    return 0 if is_marketplace_cash_payment(raw_mode) else int(configured_bps or 0)


def card_unavailable_payload(**extra: Any) -> dict:
    payload = {
        "payment_method": "card",
        "payment_status": "unavailable",
        "payment_badge": MARKETPLACE_CARD_UNAVAILABLE_BADGE,
    }
    payload.update(extra)
    return payload


def cash_checkout_payload(**extra: Any) -> dict:
    payload = {
        "payment_method": "cash",
        "payment_status": "cash_pending",
        "platform_fee_cents": 0,
        "payout_state": "cash_collect_in_person",
    }
    payload.update(extra)
    return payload
