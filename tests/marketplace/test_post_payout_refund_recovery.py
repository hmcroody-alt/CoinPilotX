"""What happens when a refund lands after the seller has already been paid.

Every existing test in `test_post_settlement_finance.py` covers a freeze that
happens *before* the transfer — a dispute, a fraud warning, a deauthorized
account. Those all work because the money is still on the platform balance and
`blocker_code` can stop it leaving.

This file covers the case where it is too late for that. A Stripe chargeback can
arrive up to 120 days after the charge, and the payout protection window is 2 days
(`STANDARD_PAYOUT_PROTECTION_DAYS`), so **no protection window can prevent this**.
It is structural, not a bug to be designed away, and the only question is whether
the system handles it or silently loses money.

It does handle it, and the mechanism is worth stating because it is entirely
emergent — no function is named for it and no comment describes it:

1. `apply_refund` debits `seller_payable:<uid>` with ``allow_negative=True``, so
   the balance goes negative by whatever the seller had already been paid.
2. A negative balance blocks further withdrawals, because `request_payout` refuses
   when ``amount_cents > available`` and the ledger's own overdraft guard
   re-checks it under a row lock.
3. Later earnings land in the same account, so they offset the debt automatically.
   When the balance climbs back above zero the seller can withdraw again, and only
   the genuine surplus.

That is a coherent recovery-by-offset policy assembled out of three unrelated
pieces, none of which mentions the others. It would survive nothing. Removing the
``allow_negative`` override, giving `seller_payable` its own allow-negative prefix,
or relaxing the balance check in `request_payout` would each break it in a way no
existing test notices, and the symptom would be money leaving the platform.

So these tests exist to make the emergent behaviour deliberate. Each one is paired
with the mechanism it depends on, so a future reader knows what they are holding.

What is NOT covered here, because it is a commercial decision and not an
engineering one: a seller who never sells again leaves the debt outstanding
forever, and nothing writes it off, invoices for it, or reports it for collection.
The reserve calculation surfaces it (`overdrawn_accounts`); what to do about it is
the owner's call. See `docs/payments/FUNDS_SEGREGATION.md`.
"""

import os
import sys
import tempfile

os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(
    tempfile.mkdtemp(prefix="mkt_post_payout_"), "test.db")
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

import pytest  # noqa: E402

from services import db  # noqa: E402
from services import marketplace_funds_segregation as segregation  # noqa: E402
from services import marketplace_settlement_service as settlements  # noqa: E402
from services.business_os.ledger import ledger  # noqa: E402
from services.business_os.payments import seller_payouts  # noqa: E402

SELLER = "77"
PAYABLE = f"seller_payable:{SELLER}"
ACCOUNT_OK = {"connected_account_id": "acct_test", "payouts_enabled": True}


@pytest.fixture(autouse=True)
def _clean():
    ledger.ensure_schema()
    settlements.ensure_schema()
    seller_payouts.ensure_schema()
    conn = db.connect()
    try:
        for table in ("ledger_entries", "ledger_transactions", "ledger_balances",
                      "marketplace_commercial_settlements", "marketplace_commercial_refunds",
                      "marketplace_payout_state_events", "seller_payout_requests"):
            try:
                conn.execute(f"DELETE FROM {table}")
            except Exception:
                pass
        conn.commit()
    finally:
        conn.close()
    yield


def _sale(transaction_id, merchandise_cents=10000):
    """One completed marketplace sale crediting the seller."""
    tx = {
        "id": transaction_id, "seller_user_id": SELLER,
        "item_type": "marketplace_product", "currency": "usd",
        "amount_cents": merchandise_cents,
        "metadata": {"quote": {
            "merchandise_net_cents": merchandise_cents, "shipping_cents": 0,
            "tax_cents": 0, "seller_shipping_credit_cents": 0,
            "buyer_total_cents": merchandise_cents, "platform_fee_cents": 0,
            "seller_earnings_cents": merchandise_cents, "platform_fee_bps": 0,
            "currency": "usd"}}}
    return settlements.settle_paid_transaction(
        tx, payout_ready=True, provider_payment_id=f"pi_{transaction_id}")


def _pay_the_seller(amount_cents, key):
    """Move earnings all the way off the platform balance, as a real payout does.

    payable -> payout_pending -> payouts_settled. The last leg is what makes this
    irreversible: after it the money is at the seller's bank and no `blocker_code`
    can reach it.
    """
    ledger.post_entry(idempotency_key=f"{key}:req", actor="test", amount_cents=amount_cents,
                      currency="usd", entry_type="payout_request", source=PAYABLE,
                      destination=f"seller_payout_pending:{SELLER}")
    ledger.post_entry(idempotency_key=f"{key}:paid", actor="test", amount_cents=amount_cents,
                      currency="usd", entry_type="payout_settled",
                      source=f"seller_payout_pending:{SELLER}",
                      destination="platform:payouts_settled")


def _balance():
    return ledger.get_balance(PAYABLE, "usd")


# --------------------------------------------------------------------------
# the loss itself
# --------------------------------------------------------------------------

def test_a_refund_after_the_money_has_left_drives_the_seller_negative():
    """This is the exposure, stated plainly.

    Not a defect to fix — it is what a chargeback after a payout *is*. PulseSoc
    has returned money to the buyer that it already gave the seller. The test
    exists so the size and shape of the hole are pinned rather than discovered.
    """
    _sale(5001)
    assert _balance() == 10000
    _pay_the_seller(10000, "payout_1")
    assert _balance() == 0, "money is now at the seller's bank"

    settlements.apply_refund(5001, provider_refund_id="re_1",
                             merchandise_refund_minor=10000)

    assert _balance() == -10000, "PulseSoc refunded the buyer out of its own pocket"


def test_the_shortfall_is_visible_in_the_reserve_rather_than_hidden():
    """It must not be netted against other sellers' credit.

    Seller 88 is owed 4000 and has nothing to do with 77's chargeback. If the
    reserve netted them, PulseSoc would be told it may withdraw money that belongs
    to 88.
    """
    _sale(5001)
    _pay_the_seller(10000, "payout_1")
    settlements.apply_refund(5001, provider_refund_id="re_1", merchandise_refund_minor=10000)
    ledger.post_entry(idempotency_key="other", actor="test", amount_cents=4000,
                      currency="usd", entry_type="marketplace_seller_earning",
                      source="external:stripe_marketplace", destination="seller_payable:88")

    report = segregation.designated_funds("usd")
    assert report["seller_payable_minor"] == 4000, "88's credit stands on its own"
    assert report["overdrawn_accounts"] == {PAYABLE: -10000}
    assert report["overdrawn_minor"] == 10000


# --------------------------------------------------------------------------
# mechanism 1: the debt blocks further withdrawal
# --------------------------------------------------------------------------

def test_a_seller_in_debt_cannot_withdraw_anything():
    """The balance check in `request_payout` is what bounds the loss.

    Without it the seller could keep drawing while already overdrawn and the hole
    would have no floor.
    """
    _sale(5001)
    _pay_the_seller(10000, "payout_1")
    settlements.apply_refund(5001, provider_refund_id="re_1", merchandise_refund_minor=10000)

    with pytest.raises(seller_payouts.PayoutError) as caught:
        seller_payouts.request_payout(SELLER, 1, requested_by="test", payout_key="k_debt",
                                      account_status=ACCOUNT_OK, currency="usd")
    assert caught.value.reason == "insufficient_balance"


def test_the_ledger_refuses_the_overdraft_even_if_the_balance_check_is_bypassed():
    """Defence in depth, and the reason `seller_payable:` has no allow-negative prefix.

    `request_payout`'s check is a read that can race. The ledger's guard re-checks
    under a row lock and is the actual enforcement, so it is tested directly
    rather than only through the caller.
    """
    _sale(5001)
    _pay_the_seller(10000, "payout_1")
    settlements.apply_refund(5001, provider_refund_id="re_1", merchandise_refund_minor=10000)

    with pytest.raises(ledger.LedgerError):
        ledger.post_entry(idempotency_key="sneaky", actor="test", amount_cents=1,
                          currency="usd", entry_type="payout_request", source=PAYABLE,
                          destination=f"seller_payout_pending:{SELLER}")


def test_seller_payable_is_not_in_the_ledgers_allow_negative_set():
    """The guard above only exists because of this. If `seller_payable:` were ever
    added to the prefix list, every overdraft test here would still pass while the
    protection they describe silently disappeared."""
    from services.business_os.ledger import ledger as ledger_module
    assert not PAYABLE.startswith(ledger_module._ALLOW_NEGATIVE_PREFIXES)


# --------------------------------------------------------------------------
# mechanism 2: later earnings repay the debt
# --------------------------------------------------------------------------

def test_the_next_sale_pays_down_the_debt_automatically():
    """Recovery by offset. Both facts live in one account, so it needs no code."""
    _sale(5001)
    _pay_the_seller(10000, "payout_1")
    settlements.apply_refund(5001, provider_refund_id="re_1", merchandise_refund_minor=10000)
    assert _balance() == -10000

    _sale(5002, merchandise_cents=6000)
    assert _balance() == -4000, "the debt shrank by exactly the new earnings"


def test_a_seller_still_in_debt_after_a_sale_still_cannot_withdraw():
    """Partial recovery is not recovery."""
    _sale(5001)
    _pay_the_seller(10000, "payout_1")
    settlements.apply_refund(5001, provider_refund_id="re_1", merchandise_refund_minor=10000)
    _sale(5002, merchandise_cents=6000)

    with pytest.raises(seller_payouts.PayoutError) as caught:
        seller_payouts.request_payout(SELLER, 1, requested_by="test", payout_key="k_partial",
                                      account_status=ACCOUNT_OK, currency="usd")
    assert caught.value.reason == "insufficient_balance"


def test_once_the_debt_clears_the_seller_can_withdraw_the_surplus_and_only_that():
    """The positive control for every refusal above.

    Without this the refusals could all be passing for an unrelated reason — a
    misconfigured account, a schema problem — and the tests would look healthy
    while proving nothing.
    """
    _sale(5001)
    _pay_the_seller(10000, "payout_1")
    settlements.apply_refund(5001, provider_refund_id="re_1", merchandise_refund_minor=10000)
    _sale(5002, merchandise_cents=14000)
    assert _balance() == 4000, "10000 debt repaid out of 14000 earned"

    # The surplus is withdrawable...
    result = seller_payouts.request_payout(SELLER, 4000, requested_by="test",
                                           payout_key="k_surplus",
                                           account_status=ACCOUNT_OK, currency="usd")
    assert result["payout"]["amount_cents"] == 4000
    assert _balance() == 0

    # ...and not a cent more.
    with pytest.raises(seller_payouts.PayoutError):
        seller_payouts.request_payout(SELLER, 1, requested_by="test", payout_key="k_over",
                                      account_status=ACCOUNT_OK, currency="usd")


def test_the_seller_never_recovers_the_refunded_money_by_waiting():
    """Time alone does not clear a debt; only earnings do.

    Guards against anyone "fixing" the negative balance by ageing it out or
    zeroing it on a schedule, which would convert a recoverable receivable into a
    silent write-off.
    """
    _sale(5001)
    _pay_the_seller(10000, "payout_1")
    settlements.apply_refund(5001, provider_refund_id="re_1", merchandise_refund_minor=10000)

    for _ in range(3):
        assert _balance() == -10000
        assert segregation.designated_funds("usd")["overdrawn_minor"] == 10000


# --------------------------------------------------------------------------
# a partial refund, which is the common case
# --------------------------------------------------------------------------

def test_a_partial_refund_after_payout_only_claws_back_its_own_share():
    _sale(5001)
    _pay_the_seller(10000, "payout_1")
    settlements.apply_refund(5001, provider_refund_id="re_partial",
                             merchandise_refund_minor=3000)
    assert _balance() == -3000


def test_a_refund_that_the_seller_can_still_cover_does_not_go_negative_at_all():
    """The ordinary case, and the boundary the negative cases are measured from.

    The seller was paid part of their balance; the refund fits in what is left, so
    no advance happens and nothing is overdrawn.
    """
    _sale(5001)
    _pay_the_seller(4000, "payout_1")
    assert _balance() == 6000

    settlements.apply_refund(5001, provider_refund_id="re_small",
                             merchandise_refund_minor=2500)

    assert _balance() == 3500
    assert segregation.designated_funds("usd")["overdrawn_accounts"] == {}
    assert segregation.designated_funds("usd")["seller_payable_minor"] == 3500
