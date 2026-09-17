import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone

os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(tempfile.mkdtemp(prefix="marketplace_settlement_"), "test.db")
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from services.business_os.ledger import ledger
from services import marketplace_settlement_service as settlement

def _tx(tx_id=1, fee_bps=1000, fee=1000, seller=9000, total=10000):
    quote = {"quote_id": f"q{tx_id}", "fee_policy_version": "MARKETPLACE_LEGACY_CURRENT",
             "payout_policy_version": "MARKETPLACE_PAYOUTS_V1", "platform_fee_bps": fee_bps,
             "merchandise_net_minor": 10000, "shipping_minor": 0, "tax_minor": 0,
             "seller_shipping_credit_minor": 0, "buyer_total_minor": total}
    return {"id": tx_id, "seller_user_id": 22, "item_type": "marketplace_product",
            "amount_cents": total, "platform_fee_cents": fee, "seller_net_cents": seller,
            "currency": "USD", "metadata_json": json.dumps({"commercial_quote": quote})}

def test_legacy_partial_refund_uses_original_ten_percent_snapshot():
    ledger.ensure_schema(); settlement.ensure_schema()
    first = settlement.settle_paid_transaction(_tx(), payout_ready=True, provider_payment_id="pi_1")
    again = settlement.settle_paid_transaction(_tx(), payout_ready=True, provider_payment_id="pi_1")
    assert first["gross_platform_fee_minor"] == 1000
    assert again["gross_platform_fee_minor"] == 1000
    out = settlement.apply_refund(1, provider_refund_id="re_1", merchandise_refund_minor=4000)
    assert out["fee_reversal_minor"] == 400
    assert out["seller_reversal_minor"] == 3600
    assert out["settlement"]["net_platform_fee_minor"] == 600
    assert settlement.apply_refund(1, provider_refund_id="re_1", merchandise_refund_minor=4000)["duplicate"]

def test_multiple_refunds_cap_and_full_reversal():
    ledger.ensure_schema(); settlement.ensure_schema()
    settlement.settle_paid_transaction(_tx(2), payout_ready=False)
    settlement.apply_refund(2, provider_refund_id="re_a", merchandise_refund_minor=4000)
    final = settlement.apply_refund(2, provider_refund_id="re_b", merchandise_refund_minor=6000)
    assert final["settlement"]["fee_reversed_minor"] == 1000
    assert final["settlement"]["seller_reversed_minor"] == 9000
    assert final["settlement"]["payout_state"] == "reversed"
    try:
        settlement.apply_refund(2, provider_refund_id="re_c", merchandise_refund_minor=1)
        assert False, "over-refund accepted"
    except settlement.SettlementError:
        pass

def test_payout_state_machine_and_idempotency():
    ledger.ensure_schema(); settlement.ensure_schema()
    assert settlement.settle_paid_transaction(_tx(3), payout_ready=True)["payout_state"] == "pending_fulfillment"
    delivered = settlement.mark_delivered(3, actor="carrier", idempotency_key="delivered:3")
    assert delivered["settlement"]["payout_state"] == "protection_hold"
    assert delivered["settlement"]["protection_ends_at"]
    held = settlement.transition_payout(3, "held", actor="system", reason="return open", idempotency_key="hold:3")
    assert held["settlement"]["payout_state"] == "held"
    assert settlement.transition_payout(3, "held", actor="system", reason="return open", idempotency_key="hold:3")["duplicate"]

def test_readiness_does_not_claim_activation():
    report = settlement.readiness()
    assert report["activatable"] == "NO"
    assert report["owner_approved"] == "NO"

def test_stripe_charge_cumulative_refund_applies_only_delta():
    import bot
    ledger.ensure_schema(); settlement.ensure_schema()
    settlement.settle_paid_transaction(_tx(4), payout_ready=True)
    bot.pulse_apply_marketplace_charge_refund({"id": "ch_4", "amount_refunded": 4000,
                                               "metadata": {"seller_transaction_id": "4"}})
    bot.pulse_apply_marketplace_charge_refund({"id": "ch_4", "amount_refunded": 6000,
                                               "metadata": {"seller_transaction_id": "4"}})
    conn = bot.db(); conn.row_factory = bot.sqlite3.Row
    try:
        total = int(dict(conn.execute("SELECT SUM(total_refund_minor) total FROM marketplace_commercial_refunds WHERE seller_transaction_id=4").fetchone())["total"])
    finally:
        conn.close()
    assert total == 6000

def _dispute(dispute_id, payment_intent, *, amount=10000, status="needs_response"):
    """A Stripe Dispute as the webhook receives it.

    Deliberately carries no `metadata`: Stripe does not copy the charge's metadata
    onto a Dispute, so the seller transaction ids the refund path reads are simply
    absent here. A fixture that supplied them would prove nothing.
    """
    return {"id": dispute_id, "object": "dispute", "payment_intent": payment_intent,
            "charge": f"ch_{dispute_id}", "amount": amount, "status": status, "metadata": {}}


def test_a_chargeback_freezes_the_payout_before_it_can_be_transferred():
    # The failure this guards is a transfer of the seller's earnings on money
    # Stripe is in the middle of taking back. A disputed settlement used to keep
    # no blocker at all, so nothing in the state machine stood between a
    # chargeback and `paid`.
    import bot
    ledger.ensure_schema(); settlement.ensure_schema()
    settlement.settle_paid_transaction(_tx(6), payout_ready=True, provider_payment_id="pi_6")
    settlement.mark_delivered(6, actor="carrier", idempotency_key="delivery:6")
    future = datetime.now(timezone.utc) + timedelta(days=3)
    # Positive control on an identical, undisputed twin. `evaluate_eligibility`
    # transitions rather than merely reporting, so it cannot be run against the
    # subject first — but without it, "not eligible" below would be satisfied by
    # a settlement that was never going to be eligible for some other reason.
    settlement.settle_paid_transaction(_tx(61), payout_ready=True, provider_payment_id="pi_61")
    settlement.mark_delivered(61, actor="carrier", idempotency_key="delivery:61")
    assert settlement.evaluate_eligibility(61, now=future)["eligible"]

    bot.pulse_apply_marketplace_dispute(_dispute("dp_6", "pi_6"), "charge.dispute.created", "evt_6")
    frozen = settlement.get_settlement(6)
    assert frozen["payout_state"] == "disputed"
    assert frozen["blocker_code"] == "dispute"
    assert not settlement.evaluate_eligibility(6, now=future)["eligible"]
    try:
        settlement.transition_payout(6, "eligible", actor="scheduler", reason="window elapsed",
                                     idempotency_key="force:6")
        assert False, "a disputed settlement reached eligible"
    except settlement.SettlementError:
        pass
    # Stripe redelivers. The second copy must not raise or double-transition.
    bot.pulse_apply_marketplace_dispute(_dispute("dp_6", "pi_6"), "charge.dispute.created", "evt_6")
    assert settlement.get_settlement(6)["payout_state"] == "disputed"


def test_a_won_dispute_releases_to_the_state_the_hold_interrupted():
    # Releasing every won dispute to `pending_fulfillment` would demand a second
    # delivery confirmation, and `mark_delivered` would dedupe it away on the
    # original idempotency key — stranding the seller's money permanently. The
    # state the hold interrupted is recovered from the immutable event log.
    import bot
    ledger.ensure_schema(); settlement.ensure_schema()
    settlement.settle_paid_transaction(_tx(7), payout_ready=True, provider_payment_id="pi_7")
    settlement.mark_delivered(7, actor="carrier", idempotency_key="delivery:7")
    bot.pulse_apply_marketplace_dispute(_dispute("dp_7", "pi_7"), "charge.dispute.created", "evt_7")
    assert settlement.get_settlement(7)["payout_state"] == "disputed"

    bot.pulse_apply_marketplace_dispute(_dispute("dp_7", "pi_7", status="won"),
                                        "charge.dispute.closed", "evt_7b")
    released = settlement.get_settlement(7)
    assert released["payout_state"] == "protection_hold"
    assert not released["blocker_code"]
    future = datetime.now(timezone.utc) + timedelta(days=3)
    assert settlement.evaluate_eligibility(7, now=future)["eligible"]


def test_a_lost_dispute_reverses_the_seller_ledger_rather_than_releasing_it():
    # Losing is not the opposite of holding. The money has left the platform
    # balance for good, so the seller's earnings have to be reversed — releasing
    # the hold would pay out money PulseSoc no longer has.
    import bot
    ledger.ensure_schema(); settlement.ensure_schema()
    settlement.settle_paid_transaction(_tx(8), payout_ready=True, provider_payment_id="pi_8")
    settlement.mark_delivered(8, actor="carrier", idempotency_key="delivery:8")
    bot.pulse_apply_marketplace_dispute(_dispute("dp_8", "pi_8"), "charge.dispute.created", "evt_8")
    bot.pulse_apply_marketplace_dispute(_dispute("dp_8", "pi_8", status="lost"),
                                        "charge.dispute.closed", "evt_8b")
    lost = settlement.get_settlement(8)
    assert lost["seller_reversed_minor"] == 9000
    assert lost["net_seller_earnings_minor"] == 0
    assert lost["net_platform_fee_minor"] == 0
    assert lost["payout_state"] == "reversed"
    assert not settlement.evaluate_eligibility(8)["eligible"]


def test_a_dispute_on_a_cart_charge_freezes_every_seller_on_it():
    # One cart checkout is one payment intent and one settlement per seller line.
    # A chargeback takes back the whole charge, so a handler that stopped at the
    # first row would leave the rest transferable.
    import bot
    ledger.ensure_schema(); settlement.ensure_schema()
    for tx_id in (9, 10):
        settlement.settle_paid_transaction(_tx(tx_id), payout_ready=True, provider_payment_id="pi_cart")
    bot.pulse_apply_marketplace_dispute(_dispute("dp_cart", "pi_cart", amount=20000),
                                        "charge.dispute.created", "evt_cart")
    assert [settlement.get_settlement(t)["payout_state"] for t in (9, 10)] == ["disputed", "disputed"]


def test_a_seller_who_onboards_after_selling_stops_being_unpayable():
    # A sale made before Connect onboarding finished opens in
    # `pending_onboarding`. Nothing ever revisited those rows: `account.updated`
    # refreshed the payout account and stopped, so the earliest sellers — the
    # ones who list before they have a bank account attached — would have had
    # their money frozen permanently while the ledger looked perfectly healthy.
    ledger.ensure_schema(); settlement.ensure_schema()
    for tx_id in (11, 12):
        assert settlement.settle_paid_transaction(
            _tx(tx_id), payout_ready=False,
            provider_payment_id=f"pi_{tx_id}")["payout_state"] == "pending_onboarding"
    # A third sale by a different seller must not be swept along.
    other = dict(_tx(13)); other["seller_user_id"] = 23
    settlement.settle_paid_transaction(other, payout_ready=False, provider_payment_id="pi_13")

    done = settlement.reconcile_seller_onboarding("22", actor="connect_webhook", reference="acct_x")
    assert len(done) == 2
    assert [settlement.get_settlement(t)["payout_state"] for t in (11, 12)] == \
        ["pending_fulfillment", "pending_fulfillment"]
    assert all(settlement.get_settlement(t)["payout_ready"] == 1 for t in (11, 12))
    assert settlement.get_settlement(13)["payout_state"] == "pending_onboarding"
    # Stripe sends `account.updated` many times. The second pass must be a no-op,
    # not a second transition out of a state the settlement has already left.
    assert settlement.reconcile_seller_onboarding("22", actor="connect_webhook", reference="acct_x") == []


def test_onboarding_reconciliation_does_not_lift_a_hold():
    # `pending_onboarding` and "blocked" are different facts. Finishing Connect
    # onboarding says nothing about a refund or chargeback, so a settlement
    # carrying a blocker must stay exactly where it is.
    ledger.ensure_schema(); settlement.ensure_schema()
    settlement.settle_paid_transaction(_tx(14), payout_ready=False, provider_payment_id="pi_14")
    settlement.place_hold(14, actor="risk", reason_code="fraud_review", idempotency_key="risk:14")
    assert settlement.reconcile_seller_onboarding("22", actor="connect_webhook", reference="acct_y") == []
    still = settlement.get_settlement(14)
    assert still["payout_state"] == "held" and still["blocker_code"] == "fraud_review"


def test_onboarding_hold_release_and_versioned_eligibility():
    ledger.ensure_schema(); settlement.ensure_schema()
    assert settlement.settle_paid_transaction(_tx(5), payout_ready=False)["payout_state"] == "pending_onboarding"
    ready = settlement.reconcile_onboarding(5, actor="connect_webhook", idempotency_key="connect:5")
    assert ready["settlement"]["payout_ready"] == 1
    settlement.mark_delivered(5, actor="carrier", idempotency_key="delivery:5")
    hold = settlement.place_hold(5, actor="risk", reason_code="return_open", idempotency_key="risk:5")
    assert hold["settlement"]["blocker_code"] == "return_open"
    settlement.release_hold(5, to_state="protection_hold", actor="risk", reason="return closed", idempotency_key="release:5")
    future = datetime.now(timezone.utc) + timedelta(days=3)
    assert settlement.evaluate_eligibility(5, now=future)["eligible"]
