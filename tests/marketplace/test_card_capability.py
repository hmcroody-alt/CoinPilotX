"""The one authority on whether a checkout may take a card.

These use a bare in-memory SQLite cursor rather than the app's connection. The
module under test takes a cursor precisely so a caller can ask inside its own
transaction, and honouring that here keeps the suite off the shared database
that the rest of ``tests/marketplace/`` cannot share a process over.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from services import marketplace_card_capability as capability
from services import marketplace_payment_pause


SELLER = 4242


@pytest.fixture
def cur():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute(
        "CREATE TABLE marketplace_sellers (user_id INTEGER, status TEXT)")
    cursor.execute(
        "CREATE TABLE seller_payout_accounts (user_id INTEGER, connected_account_id TEXT, "
        "provider_account_id TEXT, charges_enabled INTEGER, payouts_enabled INTEGER, "
        "onboarding_status TEXT, requirements_json TEXT, missing_requirements_json TEXT, "
        "updated_at TEXT, id INTEGER PRIMARY KEY AUTOINCREMENT)")
    try:
        yield cursor
    finally:
        conn.close()


def _seller(cur, *, status="approved", account="acct_live_1", charges=1, payouts=1,
            onboarding="complete", requirements=None, missing=None):
    cur.execute("DELETE FROM marketplace_sellers")
    cur.execute("DELETE FROM seller_payout_accounts")
    cur.execute("INSERT INTO marketplace_sellers (user_id,status) VALUES (?,?)", (SELLER, status))
    cur.execute(
        "INSERT INTO seller_payout_accounts (user_id,connected_account_id,charges_enabled,"
        "payouts_enabled,onboarding_status,requirements_json,missing_requirements_json,updated_at) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (SELLER, account, charges, payouts, onboarding, requirements, missing, "2026-09-19"))


@pytest.fixture(autouse=True)
def _rail_on(monkeypatch):
    """Default to the hard case: rail on, Stripe configured.

    Every test that wants a refusal then has to *cause* it, so a test cannot
    pass because the global pause happened to be shadowing the check it claims
    to be exercising.
    """
    monkeypatch.setenv(marketplace_payment_pause.CARD_PAYMENTS_ENABLED_ENV_VAR, "true")
    monkeypatch.setattr(capability, "_stripe_configured", lambda: True)


# --------------------------------------------------------------------------- #
# The happy path, so the refusals below mean something
# --------------------------------------------------------------------------- #

def test_a_fully_onboarded_seller_on_a_live_rail_may_take_a_card(cur):
    _seller(cur)
    decision = capability.evaluate(cur, seller_user_id=SELLER)
    assert decision["card_payments_available"] is True
    assert decision["reason_code"] == capability.AVAILABLE
    assert decision["badge"] is None


# --------------------------------------------------------------------------- #
# Each refusal, one at a time
# --------------------------------------------------------------------------- #

def test_the_global_flag_outranks_every_seller_fact(cur, monkeypatch):
    """A seller with nothing set up must still be told it is not their fault.

    If a seller-state reason could outrank the platform one, a seller would be
    sent to finish an onboarding that would not have unblocked them anyway.
    """
    monkeypatch.delenv(marketplace_payment_pause.CARD_PAYMENTS_ENABLED_ENV_VAR, raising=False)
    _seller(cur, status="pending", account="", charges=0, payouts=0)
    decision = capability.evaluate(cur, seller_user_id=SELLER)
    assert decision["reason_code"] == capability.FEATURE_DISABLED
    assert "nothing for you to fix" in decision["message"]


def test_an_unconfigured_stripe_refuses_before_any_seller_read(cur, monkeypatch):
    monkeypatch.setattr(capability, "_stripe_configured", lambda: False)
    _seller(cur)
    assert capability.evaluate(cur, seller_user_id=SELLER)["reason_code"] == capability.STRIPE_UNAVAILABLE


@pytest.mark.parametrize("status", ["pending", "rejected", "suspended", "withdrawn", ""])
def test_only_an_approved_seller_record_opens_the_rail(cur, status):
    """``marketplace_sellers.status`` is the authority, including after the fact.

    An approved application followed by a suspension has to close the rail, and
    it is the seller record and not the application row that carries that.
    """
    _seller(cur, status=status)
    assert capability.evaluate(cur, seller_user_id=SELLER)["reason_code"] == capability.SELLER_NOT_APPROVED


def test_a_seller_with_no_account_is_not_connected(cur):
    _seller(cur, account="")
    assert capability.evaluate(cur, seller_user_id=SELLER)["reason_code"] == capability.STRIPE_NOT_CONNECTED


@pytest.mark.parametrize("onboarding", ["restricted", "disabled", "rejected", "disconnected"])
def test_a_restricted_account_reads_as_requirements_due(cur, onboarding):
    _seller(cur, onboarding=onboarding)
    assert capability.evaluate(cur, seller_user_id=SELLER)["reason_code"] == capability.STRIPE_REQUIREMENTS_DUE


@pytest.mark.parametrize("column", ["requirements_json", "missing_requirements_json"])
def test_outstanding_requirements_are_found_in_either_column(cur, column):
    """Two spellings exist and the webhook writes the newer one.

    Checking only the original column would read a seller with an outstanding
    identity document as clear, and charge a buyer for an order that could never
    be paid out.
    """
    payload = json.dumps({"currently_due": ["individual.id_number"]})
    _seller(cur, **({"requirements": payload} if column == "requirements_json" else {"missing": payload}))
    assert capability.evaluate(cur, seller_user_id=SELLER)["reason_code"] == capability.STRIPE_REQUIREMENTS_DUE


def test_an_unparseable_requirements_blob_is_treated_as_outstanding(cur):
    """Something was written there and we cannot show it was nothing."""
    _seller(cur, requirements="{not json")
    assert capability.evaluate(cur, seller_user_id=SELLER)["reason_code"] == capability.STRIPE_REQUIREMENTS_DUE


def test_an_empty_requirements_shape_is_not_outstanding(cur):
    _seller(cur, requirements=json.dumps({"currently_due": [], "eventually_due": ["x"]}))
    assert capability.evaluate(cur, seller_user_id=SELLER)["card_payments_available"] is True


def test_charges_disabled_blocks_before_payouts_is_consulted(cur):
    _seller(cur, charges=0, payouts=0)
    assert capability.evaluate(cur, seller_user_id=SELLER)["reason_code"] == capability.CARD_CAPABILITY_DISABLED


def test_payouts_disabled_blocks_a_seller_who_could_otherwise_charge(cur):
    """Refusing here is the whole point of separate charges and transfers.

    The buyer pays the platform and the platform pays the seller afterwards. A
    seller who can take the charge but cannot receive the transfer would leave
    real money sitting in PulseSoc's balance with no lawful way out of it.
    """
    _seller(cur, charges=1, payouts=0)
    assert capability.evaluate(cur, seller_user_id=SELLER)["reason_code"] == capability.PAYOUTS_DISABLED


@pytest.mark.parametrize("status", ["draft", "paused", "removed", "sold"])
def test_an_inactive_listing_is_ineligible(cur, status):
    _seller(cur)
    decision = capability.evaluate(cur, seller_user_id=SELLER, listing={"status": status})
    assert decision["reason_code"] == capability.LISTING_INELIGIBLE


def test_stock_is_compared_against_the_quantity_actually_requested(cur):
    """Two left and three wanted is unavailable, not available.

    A bare ``> 0`` check here would take payment for three of something there
    are two of, and the refund would happen after the money had moved.
    """
    _seller(cur)
    listing = {"status": "active", "quantity_available": 2}
    assert capability.evaluate(cur, seller_user_id=SELLER, listing=listing,
                               requested_quantity=2)["card_payments_available"] is True
    assert capability.evaluate(cur, seller_user_id=SELLER, listing=listing,
                               requested_quantity=3)["reason_code"] == capability.INVENTORY_UNAVAILABLE


def test_a_missing_seller_id_is_refused_rather_than_defaulted(cur):
    for value in (None, 0, "", "abc"):
        assert capability.evaluate(cur, seller_user_id=value)["card_payments_available"] is False


# --------------------------------------------------------------------------- #
# The two properties that matter more than any individual code
# --------------------------------------------------------------------------- #

def test_a_read_failure_refuses_instead_of_raising(cur):
    """A checkout must not 500 because a capability read failed.

    And it must not charge, either. The only safe resolution of "we don't know"
    is "not today".

    The seller is seeded as fully approved first, so the refusal below is caused
    by the unreadable table and not by a seller who was never there.
    """
    _seller(cur)
    cur.execute("DROP TABLE seller_payout_accounts")
    decision = capability.evaluate(cur, seller_user_id=SELLER)
    assert decision["card_payments_available"] is False
    assert decision["reason_code"] == capability.STRIPE_UNAVAILABLE


def test_a_platform_read_failure_is_never_blamed_on_the_seller(cur):
    """``SELLER_NOT_APPROVED`` must mean the row said so, not that we couldn't read it.

    The two are one character apart in the code and worlds apart to a seller:
    one is "finish your application", the other is "our database is down". A
    seller sent to fix an application that was already approved has been given a
    task that cannot succeed.
    """
    _seller(cur)
    cur.execute("DROP TABLE marketplace_sellers")
    assert capability.evaluate(cur, seller_user_id=SELLER)["reason_code"] == capability.STRIPE_UNAVAILABLE


def test_no_seller_account_state_ever_reaches_a_buyer(cur):
    """A buyer is not entitled to know why a seller cannot take their card.

    Asserted over every reason rather than the ones we happen to emit today, so
    a reason added later cannot leak by being forgotten here.
    """
    for reason in capability.EVALUATION_ORDER:
        view = capability.buyer_view({
            "card_payments_available": False,
            "reason_code": reason,
            "message": capability.SELLER_MESSAGES[reason],
            "badge": marketplace_payment_pause.MARKETPLACE_CARD_UNAVAILABLE_BADGE,
        })
        assert view["reason_code"] not in capability.SELLER_PRIVATE_REASONS, reason
        assert view["message"] != capability.SELLER_MESSAGES[reason] or reason not in capability.SELLER_PRIVATE_REASONS


def test_every_reason_code_has_a_seller_message():
    """A missing message would KeyError inside a checkout, which is the one
    place this module promised never to raise."""
    for reason in (capability.AVAILABLE,) + capability.EVALUATION_ORDER:
        assert capability.SELLER_MESSAGES[reason].strip()


def test_the_buyer_view_preserves_the_verdict_even_as_it_hides_the_reason(cur):
    _seller(cur, charges=0)
    decision = capability.evaluate(cur, seller_user_id=SELLER)
    view = capability.buyer_view(decision)
    assert view["card_payments_available"] == decision["card_payments_available"] is False
    assert view["reason_code"] == marketplace_payment_pause.MARKETPLACE_CARD_UNAVAILABLE_CODE
