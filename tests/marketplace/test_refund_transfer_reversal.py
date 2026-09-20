"""A refund has to know where the seller's cut physically is before it acts.

Under separate charges and transfers the buyer's refund is paid out of the
*platform* balance, while the seller's share has already been moved somewhere
else — or has not. Until now the code did not ask: `apply_refund` posted a
compensating ledger entry with ``allow_negative=True`` and let the seller's
payable balance go negative regardless, and there was no
``Transfer.create_reversal`` anywhere in the repository, so nothing could ever
claw a transfer back even when the money was sitting where a reversal could
reach it.

That collapsed three genuinely different situations into one:

  before the transfer    the money never left the platform. No Stripe call
                         exists for this and inventing one would invent a debt.
  after the transfer,    the money is in the connected account's Stripe balance,
  before the payout      which is exactly what a transfer reversal draws on.
  after the payout       the money is at the seller's bank. No platform-side
                         call can reach it. It is a debt, and a debt that is
                         only ever visible as a negative balance is a debt
                         nobody can attribute, pursue, or write off.

There is a fourth, and refusing to name it would be the same error again: a
payout that exists and is in flight. Whether a reversal succeeds, fails, or
overdraws the connected account depends on timing this process cannot observe.
It moves no money and asks for a human, because an indeterminate case is the
worst possible place to act automatically.

**No Stripe call is made in this file.** The provider boundary is stubbed, and
it has to be: this deployment has no test-mode secret key, so a real
``Transfer.create_reversal`` round trip cannot be demonstrated here at all. What
is proven is which case is chosen, what kwargs would have been sent, and what is
recorded when nothing can be sent.
"""

import os
import sys
import tempfile

os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(
    tempfile.mkdtemp(prefix="mkt_refund_reversal_"), "test.db")
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

import pytest  # noqa: E402

from services import db  # noqa: E402
from services import marketplace_payout_scheduler as scheduler  # noqa: E402
from services import payment_provider  # noqa: E402
from services import marketplace_settlement_service as settlements  # noqa: E402
from services.business_os.ledger import ledger  # noqa: E402
from services.business_os.payments import incidents, seller_payouts  # noqa: E402

SELLER = "77"
PAYABLE = f"seller_payable:{SELLER}"
ACCOUNT_OK = {"connected_account_id": "acct_test", "payouts_enabled": True}


@pytest.fixture(autouse=True)
def _clean():
    ledger.ensure_schema()
    settlements.ensure_schema()
    seller_payouts.ensure_schema()
    incidents.ensure_schema()
    conn = db.connect()
    try:
        for table in ("ledger_entries", "ledger_transactions", "ledger_balances",
                      "marketplace_commercial_settlements", "marketplace_commercial_refunds",
                      "marketplace_payout_state_events", "seller_payout_requests",
                      "seller_payout_events", "financial_incidents"):
            try:
                conn.execute(f"DELETE FROM {table}")
            except Exception:
                pass
        conn.commit()
    finally:
        conn.close()
    yield


# --------------------------------------------------------------------------
# helpers: put the money in each of the four places
# --------------------------------------------------------------------------

def _sale(transaction_id, cents=10000):
    tx = {
        "id": transaction_id, "seller_user_id": SELLER,
        "item_type": "marketplace_product", "currency": "usd",
        "amount_cents": cents,
        "metadata": {"quote": {
            "merchandise_net_cents": cents, "shipping_cents": 0, "tax_cents": 0,
            "seller_shipping_credit_cents": 0, "buyer_total_cents": cents,
            "platform_fee_cents": 0, "seller_earnings_cents": cents,
            "platform_fee_bps": 0, "currency": "usd"}}}
    return settlements.settle_paid_transaction(tx, payout_ready=True,
                                               provider_payment_id=f"pi_{transaction_id}")


def _payout_row(transaction_id, cents=10000):
    """The canonical payout request, funds fenced, nothing sent to Stripe yet."""
    return seller_payouts.request_payout(
        SELLER, cents, requested_by="test",
        payout_key=f"marketplace:payout:{transaction_id}",
        account_status=ACCOUNT_OK, currency="usd")["payout"]


def _after_transfer(transaction_id, cents=10000):
    """Transfer landed; no payout created. The reversible window."""
    payout = _payout_row(transaction_id, cents)
    seller_payouts.record_transfer_id(int(payout["id"]), f"tr_{transaction_id}")
    return seller_payouts.get_payout(payout_id=int(payout["id"]))


def _after_payout(transaction_id, cents=10000):
    """Payout paid: the money is at the seller's bank and is unreachable."""
    payout = _after_transfer(transaction_id, cents)
    seller_payouts.mark_payout_submitted(int(payout["id"]),
                                         stripe_payout_id=f"po_{transaction_id}",
                                         stripe_transfer_id=f"tr_{transaction_id}")
    seller_payouts.apply_stripe_payout_event({
        "id": f"evt_{transaction_id}", "type": "payout.paid",
        "data": {"object": {"id": f"po_{transaction_id}", "status": "paid",
                            "amount": cents, "currency": "usd"}}})
    return seller_payouts.get_payout(payout_id=int(payout["id"]))


def _in_flight(transaction_id, cents=10000):
    payout = _after_transfer(transaction_id, cents)
    seller_payouts.mark_payout_submitted(int(payout["id"]),
                                         stripe_payout_id=f"po_{transaction_id}",
                                         stripe_transfer_id=f"tr_{transaction_id}")
    return seller_payouts.get_payout(payout_id=int(payout["id"]))


# --------------------------------------------------------------------------
# the provider primitive
# --------------------------------------------------------------------------

def test_the_reversal_refuses_to_run_without_stripe_configured():
    """Same guard, same shape as `create_transfer`. There is no secret key in
    this deployment, so this is also the only answer the real function can give
    here — stated as a fact, not worked around."""
    result = payment_provider.create_transfer_reversal(transfer_id="tr_1")
    assert result["ok"] is False
    assert "Stripe" in result["message"]


def test_the_reversal_requires_a_transfer_id():
    assert payment_provider.create_transfer_reversal(transfer_id="")["ok"] is False


def test_the_reversal_args_are_partial_and_keyed_per_tranche():
    """A refund is often for less than the order, and one order can be refunded
    twice. A key derived from the payout would make the second reversal a silent
    replay of the first."""
    payout = {"stripe_transfer_id": "tr_9", "payout_key": "marketplace:payout:9",
              "user_id": SELLER, "id": 1}
    first = seller_payouts.build_stripe_transfer_reversal_args(
        payout, amount_cents=2500, reversal_key="9:0:2500")
    second = seller_payouts.build_stripe_transfer_reversal_args(
        payout, amount_cents=1500, reversal_key="9:2500:4000")

    assert first["transfer_id"] == "tr_9"
    assert first["kwargs"]["amount"] == 2500
    assert first["idempotency_key"] != second["idempotency_key"]


# --------------------------------------------------------------------------
# case detection
# --------------------------------------------------------------------------

def test_a_refund_before_any_transfer_needs_no_stripe_call():
    _sale(7001)
    result = settlements.apply_refund(7001, provider_refund_id="re_1",
                                      merchandise_refund_minor=10000)

    assert result["recovery_case"] == settlements.RECOVERY_BEFORE_TRANSFER
    assert result["recovery_minor"] == 0
    assert settlements.refund_recovery_queue() == []


def test_a_refund_after_the_transfer_is_queued_for_reversal():
    _sale(7002)
    _after_transfer(7002)
    result = settlements.apply_refund(7002, provider_refund_id="re_1",
                                      merchandise_refund_minor=4000)

    assert result["recovery_case"] == settlements.RECOVERY_TRANSFER_REVERSIBLE
    assert result["recovery_minor"] == 4000
    assert [r["seller_transaction_id"] for r in settlements.refund_recovery_queue()] == [7002]


def test_a_refund_after_the_payout_is_a_recorded_debt_with_an_incident():
    """The owner's case: a seller who never sells again must not carry an
    unreconciled negative balance forever with nothing naming it."""
    _sale(7003)
    _after_payout(7003)
    result = settlements.apply_refund(7003, provider_refund_id="re_1",
                                      merchandise_refund_minor=10000)

    assert result["recovery_case"] == settlements.RECOVERY_AFTER_PAYOUT
    row = settlements.get_settlement(7003)
    assert row["refund_recovery_minor"] == 10000, "the debt is attributed to the order"

    opened = incidents.list_incidents(domain="seller_payments")["incidents"]
    debt = [i for i in opened if i["incident_type"] == incidents.NEGATIVE_BALANCE_DETECTED]
    assert debt and debt[0]["severity"] == "critical"


def test_an_unrecoverable_refund_is_never_put_in_the_reversal_queue():
    """A queue is a thing that gets drained automatically. This must not be."""
    _sale(7004)
    _after_payout(7004)
    settlements.apply_refund(7004, provider_refund_id="re_1", merchandise_refund_minor=10000)

    assert settlements.refund_recovery_queue() == []


def test_a_payout_in_flight_is_indeterminate_and_moves_no_money():
    """Neither reversible nor written off. The connected-account balance is
    already committed to a payout, so a reversal may succeed, fail, or overdraw
    depending on timing nothing here can observe."""
    _sale(7005)
    _in_flight(7005)
    result = settlements.apply_refund(7005, provider_refund_id="re_1",
                                      merchandise_refund_minor=10000)

    assert result["recovery_case"] == settlements.RECOVERY_INDETERMINATE
    assert settlements.refund_recovery_queue() == []
    assert incidents.list_incidents(domain="seller_payments")["incidents"], "a human is asked"


def test_a_refund_that_takes_nothing_from_the_seller_has_nothing_to_recover():
    """A tax-only refund reverses no seller earnings, so there is no debt."""
    _sale(7006)
    _after_transfer(7006)
    result = settlements.apply_refund(7006, provider_refund_id="re_1", other_refund_minor=100)

    assert result["recovery_case"] == settlements.RECOVERY_NONE
    assert settlements.refund_recovery_queue() == []


def test_the_case_is_stamped_at_refund_time_not_derived_later():
    """The payout keeps moving. Asking the same question tomorrow can give a
    different answer about money that has already gone."""
    _sale(7007)
    payout = _after_transfer(7007)
    settlements.apply_refund(7007, provider_refund_id="re_1", merchandise_refund_minor=3000)
    seller_payouts.mark_payout_submitted(int(payout["id"]), stripe_payout_id="po_later",
                                         stripe_transfer_id="tr_7007")

    assert settlements.get_settlement(7007)["refund_recovery_case"] == \
        settlements.RECOVERY_TRANSFER_REVERSIBLE


# --------------------------------------------------------------------------
# executing the reversal
# --------------------------------------------------------------------------

def _drain(reversal=lambda _a: {"id": "trr_1"}):
    return scheduler.run_refund_recovery_once(provider_reversal=reversal)


def test_the_reversal_is_executed_for_the_reversible_case_only():
    _sale(7008)
    _after_transfer(7008)
    settlements.apply_refund(7008, provider_refund_id="re_1", merchandise_refund_minor=4000)
    seen = []

    metrics = _drain(lambda args: seen.append(args) or {"id": "trr_1"})

    assert metrics["reversed_count"] == 1
    assert len(seen) == 1
    assert seen[0]["transfer_id"] == "tr_7008"
    assert seen[0]["kwargs"]["amount"] == 4000, "only the seller's share, not the buyer's refund"
    assert settlements.get_settlement(7008)["refund_reversal_id"] == "trr_1"


def test_a_reversed_refund_is_not_reversed_again():
    _sale(7009)
    _after_transfer(7009)
    settlements.apply_refund(7009, provider_refund_id="re_1", merchandise_refund_minor=4000)
    _drain()
    calls = []

    _drain(lambda a: calls.append(a) or {"id": "trr_2"})

    assert calls == [], "the queue compares owed against reversed, not a done flag"


def test_a_second_partial_refund_raises_the_debt_and_is_collected():
    """A 'done' flag would let the second refund's share be quietly abandoned."""
    _sale(7010)
    _after_transfer(7010)
    settlements.apply_refund(7010, provider_refund_id="re_1", merchandise_refund_minor=3000)
    _drain()
    settlements.apply_refund(7010, provider_refund_id="re_2", merchandise_refund_minor=2000)
    seen = []

    _drain(lambda a: seen.append(a) or {"id": "trr_2"})

    assert len(seen) == 1
    assert seen[0]["kwargs"]["amount"] == 2000, "only the new tranche"
    assert settlements.get_settlement(7010)["refund_reversed_minor"] == 5000


def test_a_payout_that_settled_after_the_stamp_is_skipped_not_reversed():
    """The re-check before the call is what stops a reversal being sent against
    money that has since left the connected account."""
    _sale(7011)
    payout = _after_transfer(7011)
    settlements.apply_refund(7011, provider_refund_id="re_1", merchandise_refund_minor=4000)
    seller_payouts.mark_payout_submitted(int(payout["id"]), stripe_payout_id="po_7011",
                                         stripe_transfer_id="tr_7011")
    seller_payouts.apply_stripe_payout_event({
        "id": "evt_7011", "type": "payout.paid",
        "data": {"object": {"id": "po_7011", "status": "paid",
                            "amount": 10000, "currency": "usd"}}})
    calls = []

    metrics = _drain(lambda a: calls.append(a) or {"id": "trr_x"})

    assert calls == []
    assert metrics["skipped_count"] == 1


def test_a_failed_reversal_becomes_a_named_debt_rather_than_a_retry():
    """If Stripe will not take the reversal, the money is owed. Saying so is the
    point; trying again in a loop would not change the answer and would hide it."""
    _sale(7012)
    _after_transfer(7012)
    settlements.apply_refund(7012, provider_refund_id="re_1", merchandise_refund_minor=4000)

    def _refuse(_args):
        raise RuntimeError("transfer already fully reversed")

    metrics = _drain(_refuse)

    assert metrics["failed_count"] == 1
    assert settlements.get_settlement(7012)["refund_reversed_minor"] == 0
    opened = incidents.list_incidents(domain="seller_payments")["incidents"]
    assert any(i["incident_type"] == incidents.NEGATIVE_BALANCE_DETECTED
               and i["severity"] == "critical" for i in opened)


def test_the_ledger_compensation_is_unchanged_by_any_of_this():
    """The books were already right; what was missing was the recovery and the
    attribution. A reversal must not double-count the buyer's refund."""
    _sale(7013)
    _after_transfer(7013)
    before = ledger.get_balance(PAYABLE, "usd")
    settlements.apply_refund(7013, provider_refund_id="re_1", merchandise_refund_minor=4000)
    after_refund = ledger.get_balance(PAYABLE, "usd")
    _drain()

    assert after_refund == before - 4000
    assert ledger.get_balance(PAYABLE, "usd") == after_refund
