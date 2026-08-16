"""The local StoreKit test configuration must mirror the server product catalog.

`ios/PulseSoc/Configuration.storekit` is referenced by the Xcode scheme's
LaunchAction, so *every* Xcode-launched build resolves products from this file
instead of from App Store Connect. That makes it a second, silent catalog — and
a catalog that drifts is worse than no catalog at all, because it fails in the
one direction nobody checks.

That is not hypothetical. This file previously declared both Premium
subscriptions and an **empty** `products` array, so an engineer running from
Xcode saw Premium work and Ad Credits return nothing — the exact inverse of the
device build, where Ad Credits work and Premium returns nothing. Two opposite
false readings of the same tree, depending only on how the app was launched.

These tests pin the file to `pulse_payment_router`, which is the server's
authority for what may be sold. A product added, renamed, or repriced on the
server without updating the local config now fails here rather than in someone's
afternoon of debugging.

Prices are asserted because a StoreKit test file with the wrong price makes the
subscription screen render a figure the App Store would never charge — and the
screen is explicitly built to show only store-supplied prices.
"""

from __future__ import annotations

import json
import os

import pytest

from services import pulse_payment_router as router

_STOREKIT_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "mobile-native", "ios", "PulseSoc", "Configuration.storekit",
)


@pytest.fixture(scope="module")
def config() -> dict:
    with open(_STOREKIT_PATH, "r", encoding="utf-8") as handle:
        return json.load(handle)


def _cents(display_price: str) -> int:
    return int(round(float(display_price) * 100))


def test_configuration_file_exists_and_parses(config):
    assert isinstance(config.get("products"), list)
    assert isinstance(config.get("subscriptionGroups"), list)


def test_every_ad_credit_consumable_is_present_and_priced_correctly(config):
    """Ad Credits are the flow proven working in sandbox; keep local parity."""
    local = {p["productID"]: p for p in config["products"]}
    assert set(local) == set(router.APPLE_ADCREDIT_PRODUCTS), (
        "local StoreKit consumables drifted from APPLE_ADCREDIT_PRODUCTS"
    )
    for product_id, expected_cents in router.APPLE_ADCREDIT_PRODUCTS.items():
        entry = local[product_id]
        assert entry["type"] == "Consumable", (
            f"{product_id} must stay a Consumable — ad credit is spent, not owned"
        )
        assert _cents(entry["displayPrice"]) == expected_cents


def test_every_premium_plan_is_present_as_an_auto_renewable_subscription(config):
    """The server rejects any premium transaction that is not auto-renewable.

    `apply_verified_subscription_transaction` raises unless the JWS reports
    ``Auto-Renewable Subscription``, so a local config that modelled Premium as a
    non-renewing product or a consumable would sail through the client and then
    be refused by the server — a failure that only appears after payment.
    """
    subscriptions = {
        sub["productID"]: sub
        for group in config["subscriptionGroups"]
        for sub in group["subscriptions"]
    }
    expected = {v["product_id"]: v for v in router.APPLE_PREMIUM_PRODUCTS.values()}
    assert set(subscriptions) == set(expected)

    assert not config.get("nonRenewingSubscriptions"), (
        "Premium must never be modelled as a non-renewing subscription"
    )
    for product_id, spec in expected.items():
        entry = subscriptions[product_id]
        assert entry["type"] == "RecurringSubscription"
        assert _cents(entry["displayPrice"]) == spec["price_cents"]


def test_monthly_and_annual_use_the_periods_their_plan_keys_claim(config):
    """A "monthly" plan billed yearly is a refund and a review rejection."""
    periods = {
        sub["productID"]: sub["recurringSubscriptionPeriod"]
        for group in config["subscriptionGroups"]
        for sub in group["subscriptions"]
    }
    assert periods[router.APPLE_PREMIUM_PRODUCTS["monthly"]["product_id"]] == "P1M"
    assert periods[router.APPLE_PREMIUM_PRODUCTS["annual"]["product_id"]] == "P1Y"


def test_both_plans_share_one_subscription_group(config):
    """Same group => Apple treats monthly/annual as an upgrade, not a second buy.

    In separate groups a member switching plans ends up holding two active
    subscriptions and paying twice, which is precisely the double-billing this
    system is architected to prevent.
    """
    groups = [g for g in config["subscriptionGroups"] if g.get("subscriptions")]
    assert len(groups) == 1, "Premium plans must live in exactly one subscription group"
    assert len(groups[0]["subscriptions"]) == 2


def test_product_identifiers_are_unique_across_the_whole_file(config):
    ids = [p["productID"] for p in config["products"]]
    ids += [
        sub["productID"]
        for group in config["subscriptionGroups"]
        for sub in group["subscriptions"]
    ]
    assert len(ids) == len(set(ids))
