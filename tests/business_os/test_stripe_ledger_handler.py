"""Stripe-event -> ledger handler + reconciliation worker test matrix.

Hermetic: points services.db at a throwaway SQLite file before importing it.
Runs two ways:

    python -m pytest tests/business_os/test_stripe_ledger_handler.py
    python tests/business_os/test_stripe_ledger_handler.py   # no pytest needed

Covers:
  1. funding event credits the resolved user account (amount is server-side)
  2. replayed event id is idempotent (no double-post)
  3. unmapped metadata routes funds to the suspense account, invariant intact
  4. refund event debits the account (money out)
  5. unknown / non-money event types are ignored (not failed)
  6. end-to-end through the durable inbox: enqueue -> process -> ledger
  7. reconcile_worker.run_once drains a backlog and is idempotent on re-run
  8. global double-entry invariant: all signed entries net to zero
"""

import os
import tempfile

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="busos_stripe_"), "test.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB

import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from services import db  # noqa: E402
from services.business_os import ledger  # noqa: E402
from services.business_os.payments import webhook_inbox  # noqa: E402
from services.business_os.payments import stripe_ledger_handler as slh  # noqa: E402
from services.business_os.payments import reconcile_worker  # noqa: E402


def setup_module(module=None):
    ledger.ensure_schema()
    webhook_inbox.ensure_schema()


def _reset():
    conn = db.connect()
    for t in ("ledger_entries", "ledger_transactions", "ledger_balances", "provider_webhook_events"):
        try:
            conn.execute(f"DELETE FROM {t}")
        except Exception:
            pass
    conn.commit()
    conn.close()


def _global_signed_sum():
    conn = db.connect()
    total = conn.execute(
        "SELECT COALESCE(SUM(signed_amount_cents),0) FROM ledger_entries"
    ).fetchone()[0]
    conn.close()
    return int(total)


def _pi_event(event_id, amount, user=None, currency="usd", etype="payment_intent.succeeded"):
    obj = {"amount": amount, "currency": currency, "metadata": {}}
    if user is not None:
        obj["metadata"]["pulse_user_id"] = user
    return {"id": event_id, "type": etype, "data": {"object": obj}}


# 1 -------------------------------------------------------------------------
def test_funding_credits_resolved_user():
    _reset()
    res = slh.handle_stripe_event(_pi_event("evt_f1", 5000, user=42))
    assert res["posted"] is True and res["kind"] == "funding"
    assert res["unmapped"] is False
    assert ledger.get_balance("user:42") == 5000
    # money came from the external funding source
    assert ledger.get_balance("external:stripe") == -5000
    assert _global_signed_sum() == 0


# 2 -------------------------------------------------------------------------
def test_replay_is_idempotent():
    _reset()
    a = slh.handle_stripe_event(_pi_event("evt_dup", 1200, user=7))
    b = slh.handle_stripe_event(_pi_event("evt_dup", 1200, user=7))
    assert a["duplicate"] is False
    assert b["duplicate"] is True
    assert ledger.get_balance("user:7") == 1200  # not 2400
    assert _global_signed_sum() == 0


# 3 -------------------------------------------------------------------------
def test_unmapped_routes_to_suspense():
    _reset()
    res = slh.handle_stripe_event(_pi_event("evt_u1", 999, user=None))
    assert res["posted"] is True and res["unmapped"] is True
    assert ledger.get_balance(slh.SUSPENSE) == 999
    assert ledger.get_balance("external:stripe") == -999
    assert _global_signed_sum() == 0


# 4 -------------------------------------------------------------------------
def test_refund_debits_account():
    _reset()
    # fund first so the account can cover the refund
    slh.handle_stripe_event(_pi_event("evt_pre", 3000, user=11))
    assert ledger.get_balance("user:11") == 3000
    refund = {
        "id": "evt_r1", "type": "charge.refunded",
        "data": {"object": {"amount_refunded": 1000, "currency": "usd",
                             "metadata": {"pulse_user_id": 11}}},
    }
    res = slh.handle_stripe_event(refund)
    assert res["posted"] is True and res["kind"] == "refund"
    assert ledger.get_balance("user:11") == 2000  # 3000 - 1000
    assert _global_signed_sum() == 0


# 5 -------------------------------------------------------------------------
def test_unknown_event_ignored():
    _reset()
    res = slh.handle_stripe_event(
        {"id": "evt_x", "type": "customer.updated", "data": {"object": {"id": "cus_1"}}}
    )
    assert res.get("ignored") is True
    # a $0 checkout session is also a no-op
    zero = {"id": "evt_z", "type": "checkout.session.completed",
            "data": {"object": {"amount_total": 0, "currency": "usd"}}}
    assert slh.handle_stripe_event(zero).get("ignored") is True
    # float amounts are rejected (never trust non-integer money)
    assert slh.map_stripe_event(
        {"id": "evt_flt", "type": "charge.succeeded",
         "data": {"object": {"amount": 12.34, "currency": "usd"}}}
    ) is None
    assert _global_signed_sum() == 0


# 6 -------------------------------------------------------------------------
def test_end_to_end_through_inbox():
    _reset()
    ev = _pi_event("evt_e2e", 4200, user=3)
    e1 = webhook_inbox.enqueue_event(provider="stripe", provider_event_id="evt_e2e",
                                     payload=ev, event_type=ev["type"],
                                     signature_verified=True)
    assert e1["duplicate"] is False
    r = webhook_inbox.process_event("stripe", "evt_e2e", slh.handle_stripe_event)
    assert r["status"] == "processed"
    assert ledger.get_balance("user:3") == 4200
    # reprocess (delayed re-delivery) -> skipped, ledger unchanged
    webhook_inbox.enqueue_event(provider="stripe", provider_event_id="evt_e2e", payload=ev)
    r2 = webhook_inbox.process_event("stripe", "evt_e2e", slh.handle_stripe_event)
    assert r2.get("skipped") is True
    assert ledger.get_balance("user:3") == 4200
    assert _global_signed_sum() == 0


# 7 -------------------------------------------------------------------------
def test_reconcile_worker_drains_backlog():
    _reset()
    # three events land in the inbox but are never processed inline
    for i, amt in enumerate((100, 200, 300)):
        ev = _pi_event(f"evt_b{i}", amt, user=50 + i)
        webhook_inbox.enqueue_event(provider="stripe", provider_event_id=f"evt_b{i}",
                                    payload=ev, event_type=ev["type"])
    summary = reconcile_worker.run_once("stripe")
    assert summary["provider"] == "stripe"
    assert summary["processed"] == 3
    assert ledger.get_balance("user:50") == 100
    assert ledger.get_balance("user:51") == 200
    assert ledger.get_balance("user:52") == 300
    # re-running is a no-op (all rows terminal) -> nothing processed again
    again = reconcile_worker.run_once("stripe")
    assert again["processed"] == 0
    assert ledger.get_balance("user:52") == 300
    assert _global_signed_sum() == 0


# --- standalone runner ------------------------------------------------------
def _run_standalone():
    setup_module()
    tests = [
        test_funding_credits_resolved_user,
        test_replay_is_idempotent,
        test_unmapped_routes_to_suspense,
        test_refund_debits_account,
        test_unknown_event_ignored,
        test_end_to_end_through_inbox,
        test_reconcile_worker_drains_backlog,
    ]
    passed = 0
    for t in tests:
        t()
        print(f"PASS  {t.__name__}")
        passed += 1
    print(f"\n{passed}/{len(tests)} tests passed")
    return passed == len(tests)


if __name__ == "__main__":
    ok = _run_standalone()
    raise SystemExit(0 if ok else 1)
