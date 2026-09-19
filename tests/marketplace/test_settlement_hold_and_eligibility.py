"""The hold between a confirmed delivery and the seller's money, and the sweep.

Two things were wrong before this suite existed.

The hold was the constant ``STANDARD_PAYOUT_PROTECTION_DAYS = 2``, so changing
how long a platform waits before releasing funds — a decision that moves with
dispute rates, category risk and whatever a card network asks for next — meant
editing and redeploying Python.

And nothing in production ever released a hold. ``evaluate_eligibility`` was
correct and had no caller outside tests, while the payout scheduler selected
rows that were *already* ``eligible``. Each piece worked; no piece was joined to
the next, so a delivered order stayed in ``protection_hold`` permanently and the
seller was simply never paid.
"""
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone

import pytest

os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(
    tempfile.mkdtemp(prefix="marketplace_hold_"), "test.db")
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from services.business_os.ledger import ledger  # noqa: E402
from services.business_os.marketplace import policy  # noqa: E402
from services import marketplace_settlement_service as settlement  # noqa: E402

HOURS = policy.SETTLEMENT_HOLD_HOURS_ENV_VAR


@pytest.fixture(autouse=True)
def _unconfigured_hold(monkeypatch):
    """Every test states its own hold; none inherits the developer's shell."""
    monkeypatch.delenv(HOURS, raising=False)


def _tx(tx_id, total=10000, fee=1000, seller=9000):
    quote = {"quote_id": f"q{tx_id}", "fee_policy_version": "MARKETPLACE_LEGACY_CURRENT",
             "payout_policy_version": "MARKETPLACE_PAYOUTS_V1", "platform_fee_bps": 1000,
             "merchandise_net_minor": 10000, "shipping_minor": 0, "tax_minor": 0,
             "seller_shipping_credit_minor": 0, "buyer_total_minor": total}
    return {"id": tx_id, "seller_user_id": 22, "item_type": "marketplace_product",
            "amount_cents": total, "platform_fee_cents": fee, "seller_net_cents": seller,
            "currency": "USD", "metadata_json": json.dumps({"commercial_quote": quote})}


def _delivered(tx_id):
    ledger.ensure_schema(); settlement.ensure_schema()
    settlement.settle_paid_transaction(_tx(tx_id), payout_ready=True)
    return settlement.mark_delivered(tx_id, actor="carrier", idempotency_key=f"d:{tx_id}")


def _revoke_payout_readiness(tx_id):
    """A seller whose payouts_enabled is withdrawn after the sale.

    Not reachable by settling with ``payout_ready=False`` — that opens in
    ``pending_onboarding``, which cannot reach ``protection_hold`` at all. The
    order this models is one already delivered when Stripe pulled the capability.
    """
    from services import db
    conn = db.connect()
    try:
        conn.execute("UPDATE marketplace_commercial_settlements SET payout_ready=0 "
                     "WHERE seller_transaction_id=?", (tx_id,)); conn.commit()
    finally:
        conn.close()


# --- the hold is configuration, not a constant -----------------------------

def test_an_unconfigured_deployment_holds_exactly_as_long_as_it_used_to():
    assert policy.settlement_hold_hours() == policy.STANDARD_PAYOUT_PROTECTION_DAYS * 24


def test_the_owner_can_set_the_hold_without_editing_python(monkeypatch):
    monkeypatch.setenv(HOURS, "72")
    assert policy.settlement_hold_hours() == 72


def test_waiving_the_hold_is_honoured_rather_than_overridden(monkeypatch):
    # An explicit zero is a decision the owner is entitled to make. Quietly
    # substituting the default would make the variable a lie.
    monkeypatch.setenv(HOURS, "0")
    assert policy.settlement_hold_hours() == 0


@pytest.mark.parametrize("value", ["", "   ", "abc", "-1", "-48", "2.5", "48h"])
def test_an_unusable_hold_falls_back_to_the_default_and_never_to_zero(monkeypatch, value):
    # The opposite of how the fulfillment timeouts fail, on purpose: there a bad
    # value leaves an order where it is, here it would pay a seller instantly.
    monkeypatch.setenv(HOURS, value)
    assert policy.settlement_hold_hours() == policy.STANDARD_PAYOUT_PROTECTION_DAYS * 24


def test_the_hold_is_read_per_call_not_captured_at_import(monkeypatch):
    monkeypatch.setenv(HOURS, "1")
    assert policy.settlement_hold_hours() == 1
    monkeypatch.setenv(HOURS, "5")
    assert policy.settlement_hold_hours() == 5


def test_a_delivery_stamps_the_configured_hold_onto_the_settlement(monkeypatch):
    monkeypatch.setenv(HOURS, "6")
    before = datetime.now(timezone.utc)
    record = _delivered(701)["settlement"]
    ends = datetime.fromisoformat(str(record["protection_ends_at"]).replace("Z", "+00:00"))
    assert timedelta(hours=6) - timedelta(minutes=1) <= ends - before <= timedelta(hours=6, minutes=1)


def test_the_settlement_hold_is_not_the_buyers_return_window(monkeypatch):
    # Two unrelated policies must not share one stored number: shortening the
    # payout hold would otherwise move every buyer's return deadline with it.
    monkeypatch.setenv(HOURS, "1")
    record = _delivered(702)["settlement"]
    ends = datetime.fromisoformat(str(record["protection_ends_at"]).replace("Z", "+00:00"))
    delivered = datetime.fromisoformat(str(record["delivered_at"]).replace("Z", "+00:00"))
    returns_close = policy.return_window_closes_at(delivered_at=record["delivered_at"])
    assert ends - delivered < timedelta(hours=2)
    assert datetime.fromisoformat(returns_close.replace("Z", "+00:00")) - delivered > timedelta(days=13)


# --- a refusal says why ----------------------------------------------------

def test_a_settlement_still_inside_its_hold_says_so(monkeypatch):
    monkeypatch.setenv(HOURS, "48")
    _delivered(710)
    out = settlement.evaluate_eligibility(710)
    assert not out["eligible"]
    assert out["reason_code"] == settlement.HOLD_NOT_ELAPSED


def test_a_seller_who_never_onboarded_is_told_that_and_not_about_the_clock(monkeypatch):
    monkeypatch.setenv(HOURS, "1")
    _delivered(711)
    _revoke_payout_readiness(711)
    future = datetime.now(timezone.utc) + timedelta(days=3)
    out = settlement.evaluate_eligibility(711, now=future)
    assert not out["eligible"]
    assert out["reason_code"] == settlement.SELLER_NOT_PAYOUT_READY


def test_a_held_settlement_names_the_hold_and_not_the_window(monkeypatch):
    monkeypatch.setenv(HOURS, "1")
    _delivered(712)
    settlement.place_hold(712, actor="risk", reason_code="CHARGEBACK_REVIEW",
                          idempotency_key="hold:712")
    future = datetime.now(timezone.utc) + timedelta(days=3)
    out = settlement.evaluate_eligibility(712, now=future)
    assert not out["eligible"]
    assert settlement.PAYOUT_BLOCKED in out["blockers"]


def test_every_reason_is_reported_not_only_the_first(monkeypatch):
    # A seller told to fix one thing and then refused for a second has been told
    # the truth twice and helped neither time.
    monkeypatch.setenv(HOURS, "48")
    _delivered(713)
    _revoke_payout_readiness(713)
    out = settlement.evaluate_eligibility(713)
    assert set(out["blockers"]) == {settlement.SELLER_NOT_PAYOUT_READY,
                                    settlement.HOLD_NOT_ELAPSED}


def test_a_settlement_that_is_not_holding_anything_is_refused_by_state(monkeypatch):
    monkeypatch.setenv(HOURS, "1")
    ledger.ensure_schema(); settlement.ensure_schema()
    settlement.settle_paid_transaction(_tx(714), payout_ready=True)
    out = settlement.evaluate_eligibility(714)
    assert not out["eligible"]
    assert out["reason_code"] == settlement.NOT_IN_PROTECTION_HOLD


def test_a_missing_hold_end_refuses_instead_of_raising(monkeypatch):
    """The bug this branch found: one bad row stopped the whole queue.

    A settlement reaches protection_hold without a delivery whenever an admin
    releases a hold into that state, so protection_ends_at is NULL. The old code
    fed that straight to fromisoformat, which raised ValueError — not
    SettlementError — out of the sweep, taking every settlement behind it down.
    """
    monkeypatch.setenv(HOURS, "1")
    ledger.ensure_schema(); settlement.ensure_schema()
    settlement.settle_paid_transaction(_tx(715), payout_ready=True)
    settlement.place_hold(715, actor="risk", reason_code="MANUAL", idempotency_key="h:715")
    settlement.release_hold(715, to_state="protection_hold", actor="risk",
                            reason="cleared", idempotency_key="r:715")
    record = settlement.get_settlement(715)
    assert not record["protection_ends_at"]

    out = settlement.evaluate_eligibility(715, now=datetime.now(timezone.utc) + timedelta(days=30))
    assert not out["eligible"]
    assert out["reason_code"] == settlement.HOLD_END_UNKNOWN


def test_an_unknown_hold_end_is_not_treated_as_an_elapsed_one():
    # Unknown must fail towards the money staying put. Reading a missing
    # timestamp as "long ago" would release funds no delivery ever justified.
    blockers = settlement.eligibility_blockers(
        {"payout_state": "protection_hold", "payout_ready": 1, "protection_ends_at": None})
    assert blockers == [settlement.HOLD_END_UNKNOWN]


def test_a_released_settlement_reports_no_reason_at_all(monkeypatch):
    monkeypatch.setenv(HOURS, "1")
    _delivered(716)
    out = settlement.evaluate_eligibility(716, now=datetime.now(timezone.utc) + timedelta(days=3))
    assert out["eligible"]
    assert out["reason_code"] == settlement.ELIGIBLE
    assert out["blockers"] == []
    assert out["settlement"]["payout_state"] == "eligible"


# --- the sweep is the caller that never existed ----------------------------

def test_the_sweep_releases_a_settlement_whose_hold_has_elapsed(monkeypatch):
    monkeypatch.setenv(HOURS, "1")
    _delivered(720)
    assert settlement.get_settlement(720)["payout_state"] == "protection_hold"
    metrics = settlement.sweep_eligibility(now=datetime.now(timezone.utc) + timedelta(days=3))
    assert metrics["released"] >= 1
    assert settlement.get_settlement(720)["payout_state"] == "eligible"


def test_the_sweep_leaves_a_settlement_still_inside_its_hold_alone(monkeypatch):
    monkeypatch.setenv(HOURS, "48")
    _delivered(721)
    settlement.sweep_eligibility()
    assert settlement.get_settlement(721)["payout_state"] == "protection_hold"


def test_the_sweep_tallies_why_it_refused_rather_than_reporting_nothing(monkeypatch):
    monkeypatch.setenv(HOURS, "48")
    _delivered(722)
    metrics = settlement.sweep_eligibility()
    # A deployment where nothing is moving has to be distinguishable from one
    # where nothing is due, or an outage looks exactly like a quiet week.
    assert metrics["blocked"] >= 1
    assert metrics["reasons"].get(settlement.HOLD_NOT_ELAPSED, 0) >= 1


def test_one_unreleasable_row_does_not_stop_the_ones_behind_it(monkeypatch):
    monkeypatch.setenv(HOURS, "1")
    ledger.ensure_schema(); settlement.ensure_schema()
    settlement.settle_paid_transaction(_tx(730), payout_ready=True)
    settlement.place_hold(730, actor="risk", reason_code="MANUAL", idempotency_key="h:730")
    settlement.release_hold(730, to_state="protection_hold", actor="risk",
                            reason="cleared", idempotency_key="r:730")
    _delivered(731)

    metrics = settlement.sweep_eligibility(now=datetime.now(timezone.utc) + timedelta(days=3))
    assert metrics["errors"] == 0
    assert settlement.get_settlement(731)["payout_state"] == "eligible"
    assert settlement.get_settlement(730)["payout_state"] == "protection_hold"


def test_sweeping_twice_does_not_release_the_same_settlement_twice(monkeypatch):
    monkeypatch.setenv(HOURS, "1")
    _delivered(732)
    future = datetime.now(timezone.utc) + timedelta(days=3)
    first = settlement.sweep_eligibility(now=future)
    second = settlement.sweep_eligibility(now=future)
    assert first["released"] >= 1
    # The second pass cannot see it: the select is scoped to protection_hold.
    assert settlement.get_settlement(732)["payout_state"] == "eligible"
    assert second["considered"] < first["considered"] or second["released"] == 0


def test_the_sweep_never_moves_money_itself(monkeypatch):
    """Eligibility is permission to pay, not a payment.

    The sweep runs unattended, so if it could reach the provider a scheduling
    bug would become a money bug. It transitions state and stops.
    """
    monkeypatch.setenv(HOURS, "1")
    _delivered(733)
    from services.business_os.payments import seller_payouts

    calls = []
    original = seller_payouts.request_payout
    monkeypatch.setattr(seller_payouts, "request_payout",
                        lambda *a, **k: calls.append(a) or original(*a, **k))
    settlement.sweep_eligibility(now=datetime.now(timezone.utc) + timedelta(days=3))
    assert calls == []
