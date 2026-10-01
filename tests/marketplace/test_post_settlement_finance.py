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
