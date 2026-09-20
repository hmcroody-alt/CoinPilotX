"""A failed payout is no longer a dead end, and it is not an infinite loop either.

Before this, `marketplace_payout_scheduler.run_once` moved a settlement to
`failed` when the provider refused, and nothing ever looked at it again: both
work selectors read ``payout_state='eligible'``, so `failed` was terminal in
practice even though `ALLOWED_TRANSITIONS` had allowed ``failed -> scheduled``
all along. A seller whose transfer hit a Stripe rate limit simply never got paid,
and no incident said so.

The fix is not "retry failures". Retrying a closed bank account is the same
mistake repeated on a schedule, and it burns the budget that a real transient
failure needs. So every failure is classified first, and the classification
decides which of three things happens:

  transient      retry with backoff, tell nobody
  seller action  stop, notify the seller, fence the row
  anything else  stop, fence the row, open an incident

"Anything else" includes every failure the classifier does not recognise. That is
the load-bearing default and the tests below pin it: an unknown failure is
**not** retried. The cost of that choice is a delay on a failure we could have
retried; the cost of the opposite choice is an unrecognised, permanent failure
repeated until a bounded budget runs out, on every settlement, forever — with
nobody told, because the retry queue looks like it is working.

These tests drive the scheduler with injected provider callables. **No Stripe
call is made anywhere in this file**, and none can be: there is no test-mode
secret key in this deployment, so the provider boundary is a stub and what is
asserted is the state machine around it, not Stripe's behaviour.
"""

import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone

os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(
    tempfile.mkdtemp(prefix="mkt_payout_retry_"), "test.db")
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

import pytest  # noqa: E402

from services import db  # noqa: E402
from services import marketplace_payout_scheduler as scheduler  # noqa: E402
from services import marketplace_payout_worker as worker  # noqa: E402
from services import marketplace_settlement_service as settlements  # noqa: E402
from services.business_os.ledger import ledger  # noqa: E402
from services.business_os.payments import incidents, seller_payouts  # noqa: E402

SELLER = "77"
ACCOUNT_OK = {"connected_account_id": "acct_test", "payouts_enabled": True}
FAST_POLICY = {"max_attempts": 3, "base_seconds": 60, "max_seconds": 600}


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
# fixtures for the world the scheduler reads
# --------------------------------------------------------------------------

def _eligible_sale(transaction_id, cents=10000):
    """A settlement sitting in `eligible`, which is what `run_once` selects."""
    tx = {
        "id": transaction_id, "seller_user_id": SELLER,
        "item_type": "marketplace_product", "currency": "usd",
        "amount_cents": cents,
        "metadata": {"quote": {
            "merchandise_net_cents": cents, "shipping_cents": 0, "tax_cents": 0,
            "seller_shipping_credit_cents": 0, "buyer_total_cents": cents,
            "platform_fee_cents": 0, "seller_earnings_cents": cents,
            "platform_fee_bps": 0, "currency": "usd"}}}
    settlements.settle_paid_transaction(tx, payout_ready=True,
                                        provider_payment_id=f"pi_{transaction_id}")
    conn = db.connect()
    try:
        conn.execute("UPDATE marketplace_commercial_settlements SET payout_state='eligible', "
                     "payout_ready=1, blocker_code=NULL WHERE seller_transaction_id=?",
                     (transaction_id,))
        conn.commit()
    finally:
        conn.close()
    return settlements.get_settlement(transaction_id)


class _StripeShaped(Exception):
    """Stands in for a Stripe exception: class name plus a ``code``.

    The classifier duck-types both, exactly as `marketplace_payment_errors` does,
    so nothing here needs the ``stripe`` package — which is fortunate, because a
    real Stripe object would need a key this deployment does not have.
    """

    def __init__(self, name, code=""):
        super().__init__(name)
        self.code = code
        self.__class__ = type(name, (_StripeShaped,), {}) if name else _StripeShaped


def _raiser(exc):
    def _call(_args):
        raise exc
    return _call


def _ok_transfer(_args):
    return {"id": "tr_ok"}


def _ok_payout(_args):
    return {"id": "po_ok"}


def _run(transfer=_ok_transfer, payout=_ok_payout, policy=FAST_POLICY):
    return scheduler.run_once(
        account_resolver=lambda _uid: ACCOUNT_OK,
        provider_transfer=transfer, provider_create=payout, retry_policy=policy)


def _rewind(transaction_id, seconds=3600):
    """Pull a settlement's next-attempt time into the past.

    The backoff is real time, so a test that wanted to observe the retry would
    otherwise have to wait sixty seconds. Rewinding the stored deadline tests the
    same selector the scheduler uses.
    """
    past = (datetime.now(timezone.utc) - timedelta(seconds=seconds)) \
        .strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    conn = db.connect()
    try:
        conn.execute("UPDATE marketplace_commercial_settlements SET payout_next_attempt_at=? "
                     "WHERE seller_transaction_id=?", (past, transaction_id))
        conn.commit()
    finally:
        conn.close()


# --------------------------------------------------------------------------
# classification
# --------------------------------------------------------------------------

def test_an_unrecognised_failure_is_not_retried():
    """The fail-closed default, asserted first because everything rests on it."""
    verdict = seller_payouts.classify_payout_failure(RuntimeError("something new"))
    assert verdict["failure_class"] == seller_payouts.PERMANENT
    assert verdict["retryable"] is False
    assert verdict["failure_code"] == seller_payouts.UNCLASSIFIED_FAILURE_CODE


@pytest.mark.parametrize("code", ["account_closed", "no_account", "invalid_account_number",
                                  "debit_not_authorized", "declined"])
def test_failures_only_the_seller_can_fix_are_never_retried(code):
    verdict = seller_payouts.classify_payout_failure(_StripeShaped("InvalidRequestError", code))
    assert verdict["failure_class"] == seller_payouts.SELLER_ACTION
    assert verdict["retryable"] is False
    assert verdict["seller_action"] is True


@pytest.mark.parametrize("name", ["APIConnectionError", "RateLimitError", "APIError"])
def test_transient_provider_failures_are_retryable(name):
    verdict = seller_payouts.classify_payout_failure(_StripeShaped(name))
    assert verdict["failure_class"] == seller_payouts.RETRYABLE
    assert verdict["retryable"] is True


@pytest.mark.parametrize("name", ["AuthenticationError", "PermissionError",
                                  "InvalidRequestError", "IdempotencyError"])
def test_platform_configuration_failures_are_permanent(name):
    """A key that is not enabled for Connect fails identically on every attempt.

    This is the failure this platform is most likely to hit first, which is
    precisely why retrying it would be worst: an operator would watch a retry
    queue drain instead of reading an incident naming the real problem.
    """
    verdict = seller_payouts.classify_payout_failure(_StripeShaped(name))
    assert verdict["failure_class"] == seller_payouts.PERMANENT
    assert verdict["retryable"] is False


def test_the_code_outranks_the_exception_class():
    """A seller-fixable code inside a generic API error is still seller-fixable."""
    verdict = seller_payouts.classify_payout_failure(_StripeShaped("APIError", "account_closed"))
    assert verdict["failure_class"] == seller_payouts.SELLER_ACTION


# --------------------------------------------------------------------------
# the retry itself
# --------------------------------------------------------------------------

def test_a_transient_failure_is_retried_and_succeeds():
    """The behaviour that did not exist before: `failed` is re-offered."""
    _eligible_sale(6001)
    _run(transfer=_raiser(_StripeShaped("APIConnectionError")))
    assert settlements.get_settlement(6001)["payout_state"] == "failed"

    _rewind(6001)
    metrics = _run()

    assert metrics["retry_count"] == 1
    assert metrics["transferred_count"] == 1
    assert settlements.get_settlement(6001)["payout_state"] == "scheduled"


def test_a_failure_before_its_backoff_elapses_is_left_alone():
    """Backoff is a real wait, not a label."""
    _eligible_sale(6002)
    _run(transfer=_raiser(_StripeShaped("APIConnectionError")))

    metrics = _run()

    assert metrics["retry_count"] == 0, "the next attempt time has not arrived"
    assert settlements.get_settlement(6002)["payout_state"] == "failed"


def test_the_retry_reuses_the_payout_key_so_stripe_cannot_transfer_twice():
    """The single most dangerous thing a retry could get wrong.

    Stripe's idempotency key is derived from `payout_key`. If a retry minted a
    new key, the transfer that already succeeded before the payout leg failed
    would be created a second time and the seller would be paid twice. Asserting
    on the captured kwargs rather than on the source text, because what matters
    is the value that would reach Stripe.
    """
    _eligible_sale(6003)
    seen = []

    def _capture_then_fail(args):
        seen.append(args["idempotency_key"])
        return {"id": "tr_1"}

    _run(transfer=_capture_then_fail, payout=_raiser(_StripeShaped("APIConnectionError")))
    _rewind(6003)
    _run(transfer=_capture_then_fail)

    assert len(seen) == 2
    assert seen[0] == seen[1] == "seller_transfer:marketplace:payout:6003"


def test_the_transfer_id_survives_a_failure_of_the_payout_leg():
    """The gap the scheduler's own comment admitted to.

    Between the transfer and the payout the platform has irreversibly moved
    money, and until now nothing recorded that unless *both* legs succeeded. A
    refund later has to know a transfer exists before it can decide to reverse
    it; with the id dropped, the books said it did not.
    """
    _eligible_sale(6004)
    _run(transfer=lambda _a: {"id": "tr_only"},
         payout=_raiser(_StripeShaped("APIConnectionError")))

    payout = seller_payouts.get_payout(payout_key="marketplace:payout:6004")
    assert payout["stripe_transfer_id"] == "tr_only"
    assert payout["status"] == "pending", "the transfer id is recorded without advancing state"


def test_the_attempt_count_is_persisted_not_held_in_memory():
    """A redeploy must not hand a stranded payout a fresh budget."""
    _eligible_sale(6005)
    _run(transfer=_raiser(_StripeShaped("APIConnectionError")))
    first = settlements.get_settlement(6005)["payout_attempt_count"]
    _rewind(6005)
    _run(transfer=_raiser(_StripeShaped("APIConnectionError")))

    assert first == 1
    assert settlements.get_settlement(6005)["payout_attempt_count"] == 2


def test_backoff_grows_between_attempts():
    _eligible_sale(6006)
    _run(transfer=_raiser(_StripeShaped("APIConnectionError")))
    first_gap = _gap(6006)
    _rewind(6006)
    _run(transfer=_raiser(_StripeShaped("APIConnectionError")))

    assert _gap(6006) > first_gap


def _gap(transaction_id):
    row = settlements.get_settlement(transaction_id)
    at = datetime.strptime(row["payout_next_attempt_at"], "%Y-%m-%dT%H:%M:%S.%fZ") \
        .replace(tzinfo=timezone.utc)
    return (at - datetime.now(timezone.utc)).total_seconds()


# --------------------------------------------------------------------------
# where a retry stops
# --------------------------------------------------------------------------

def test_the_budget_runs_out_and_the_settlement_is_fenced_with_an_incident():
    """Exhaustion is an operator's problem, not a log line.

    The money is not returned to the payable balance and not written off. It
    stays fenced exactly where it was, and the row is blocked so neither selector
    offers it again — which means `release_hold` is the only way back, by a human
    who has read the incident.
    """
    _eligible_sale(6007)
    for _ in range(FAST_POLICY["max_attempts"]):
        _rewind(6007)
        _run(transfer=_raiser(_StripeShaped("APIConnectionError")))

    row = settlements.get_settlement(6007)
    assert row["payout_attempt_count"] == FAST_POLICY["max_attempts"]
    assert row["payout_next_attempt_at"] is None, "no further attempt is scheduled"
    assert row["blocker_code"] == scheduler.EXHAUSTED_BLOCKER
    assert row["payout_state"] == "held"

    opened = incidents.list_incidents(domain="seller_payments")["incidents"]
    assert any(i["incident_type"] == incidents.SUSPENSE_FUNDS_HELD
               and i["severity"] == "critical" for i in opened)


def test_an_exhausted_settlement_is_not_offered_again():
    _eligible_sale(6008)
    for _ in range(FAST_POLICY["max_attempts"]):
        _rewind(6008)
        _run(transfer=_raiser(_StripeShaped("APIConnectionError")))

    metrics = _run()

    assert metrics["eligible_count"] == 0
    assert metrics["retry_count"] == 0


def test_a_permanent_failure_is_fenced_on_the_first_attempt():
    """Never retry something that cannot succeed."""
    _eligible_sale(6009)
    _run(transfer=_raiser(_StripeShaped("AuthenticationError")))

    row = settlements.get_settlement(6009)
    assert row["payout_attempt_count"] == 1
    assert row["payout_next_attempt_at"] is None
    assert row["blocker_code"] == scheduler.UNRECOVERABLE_BLOCKER
    assert incidents.list_incidents(domain="seller_payments")["incidents"], "an operator is told"


def test_an_unclassified_failure_is_fenced_rather_than_retried():
    """The fail-closed default, observed through the scheduler rather than the classifier."""
    _eligible_sale(6010)
    _run(transfer=_raiser(RuntimeError("unrecognised")))

    row = settlements.get_settlement(6010)
    assert row["payout_next_attempt_at"] is None
    assert row["payout_failure_class"] == seller_payouts.PERMANENT
    assert row["blocker_code"] == scheduler.UNRECOVERABLE_BLOCKER


def test_a_seller_action_failure_notifies_the_seller_through_the_existing_engine(monkeypatch):
    """One notification path, not a second one invented for this feature."""
    from services import payments_notifications

    sent = []
    monkeypatch.setattr(payments_notifications, "emit",
                        lambda event, recipient, context=None, **kw: sent.append(
                            (event, recipient, dict(context or {}))) or {"ok": True})

    _eligible_sale(6011, cents=4200)
    _run(transfer=_raiser(_StripeShaped("InvalidRequestError", "account_closed")))

    assert len(sent) == 1
    event, recipient, context = sent[0]
    assert event == payments_notifications.PAYOUT_FAILED
    assert recipient == int(SELLER)
    assert context["failure_reason"] == "account_closed"
    assert context["amount_cents"] == 4200
    assert settlements.get_settlement(6011)["payout_next_attempt_at"] is None


def test_a_transient_failure_does_not_notify_the_seller(monkeypatch):
    """A retry that is going to work is not news, and telling the seller is worse
    than saying nothing: they would be asked to fix something that is not broken."""
    from services import payments_notifications

    sent = []
    monkeypatch.setattr(payments_notifications, "emit",
                        lambda *a, **kw: sent.append(a) or {"ok": True})

    _eligible_sale(6012)
    _run(transfer=_raiser(_StripeShaped("APIConnectionError")))

    assert sent == []


def test_a_settlement_whose_canonical_payout_stripe_already_terminated_is_not_resubmitted():
    """Stripe's own failure webhook is terminal for the payout row.

    Resubmitting under the same key is impossible and minting a new one would
    create a second transfer. So it is fenced for an operator instead — the
    'do not automatically move money where the correct action is unestablished'
    rule, applied to the one case where the tempting move is a duplicate payment.
    """
    _eligible_sale(6013)
    _run(transfer=_raiser(_StripeShaped("APIConnectionError")))
    payout = seller_payouts.get_payout(payout_key="marketplace:payout:6013")
    seller_payouts.fail_payout(int(payout["id"]), failure_code="account_closed")
    _rewind(6013)

    calls = []
    _run(transfer=lambda a: calls.append(a) or {"id": "tr_x"})

    assert calls == [], "no Stripe call was attempted"
    row = settlements.get_settlement(6013)
    assert row["payout_failure_code"] == "payout_terminal"
    assert row["blocker_code"] == scheduler.UNRECOVERABLE_BLOCKER


# --------------------------------------------------------------------------
# success, and the tunables
# --------------------------------------------------------------------------

def test_a_successful_attempt_stops_the_retry_but_keeps_the_attempts_spent():
    """Attempts spent stay spent: a row that needed three tries has shown us
    something, and should not meet its next failure with a full budget."""
    _eligible_sale(6014)
    _run(transfer=_raiser(_StripeShaped("APIConnectionError")))
    _rewind(6014)
    _run()

    row = settlements.get_settlement(6014)
    assert row["payout_next_attempt_at"] is None
    assert row["payout_attempt_count"] == 1


def test_the_retry_tunables_are_clamped(monkeypatch):
    """Same house rule as every other switch in this worker: a typo resolves to
    the safe value rather than to whatever was typed."""
    monkeypatch.setenv(worker.RETRY_MAX_ATTEMPTS_ENV_VAR, "9999")
    monkeypatch.setenv(worker.RETRY_BASE_SECONDS_ENV_VAR, "1")
    monkeypatch.setenv(worker.RETRY_MAX_SECONDS_ENV_VAR, "not a number")

    policy = worker.retry_policy()

    assert policy["max_attempts"] == worker.MAX_RETRY_MAX_ATTEMPTS
    assert policy["base_seconds"] == worker.MIN_RETRY_BASE_SECONDS
    assert policy["max_seconds"] == worker.DEFAULT_RETRY_MAX_SECONDS


def test_backoff_is_capped_by_the_policy_maximum():
    """Without the cap a five-attempt budget on a fifteen-minute base puts the
    last attempt four hours out, for a failure that was over in seconds."""
    _eligible_sale(6015)
    _run(transfer=_raiser(_StripeShaped("APIConnectionError")),
         policy={"max_attempts": 5, "base_seconds": 600, "max_seconds": 601})

    assert _gap(6015) <= 601
