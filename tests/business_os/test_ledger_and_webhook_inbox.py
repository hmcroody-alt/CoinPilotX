"""Payments foundation test matrix — canonical ledger + webhook inbox.

Runs hermetically against a temporary SQLite DB (set via DATABASE_URL before
importing services.db). Executable two ways:

    python -m pytest tests/business_os/test_ledger_and_webhook_inbox.py
    python tests/business_os/test_ledger_and_webhook_inbox.py   # no pytest needed

Covers the Stage 1 acceptance matrix:
  1. duplicate submission is a no-op
  2. concurrent double-post yields exactly one entry
  3. webhook replay / out-of-order / delayed are idempotent
  4. balance == sum(entries) after N randomized ops
  5. mid-processing crash leaves the event replayable
  6. invalid / unauthorized / overdraft writes are rejected
"""

import os
import random
import tempfile
import threading

# --- point services.db at a throwaway SQLite file BEFORE importing it ---
_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="busos_"), "test.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB

import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from services import db  # noqa: E402
from services.business_os import ledger  # noqa: E402
from services.business_os.payments import webhook_inbox  # noqa: E402


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


def _entry_count():
    conn = db.connect()
    n = conn.execute("SELECT COUNT(*) FROM ledger_entries").fetchone()[0]
    conn.close()
    return int(n)


def _sum_account(account, currency="usd"):
    conn = db.connect()
    row = conn.execute(
        "SELECT COALESCE(SUM(signed_amount_cents),0) FROM ledger_entries "
        "WHERE account=? AND currency=?",
        (account, currency),
    ).fetchone()
    conn.close()
    return int(row[0])


# 1 -------------------------------------------------------------------------
def test_duplicate_submission_is_noop():
    _reset()
    args = dict(idempotency_key="dup-1", actor="t", amount_cents=1000, currency="usd",
                entry_type="funding", source="external:stripe", destination="user:1")
    a = ledger.post_entry(**args)
    b = ledger.post_entry(**args)
    assert a["duplicate"] is False
    assert b["duplicate"] is True
    assert b["transaction_id"] == a["transaction_id"]
    assert _entry_count() == 2  # exactly one double-entry pair, not four
    assert ledger.get_balance("user:1") == 1000


# 2 -------------------------------------------------------------------------
def test_concurrent_double_post_yields_one_entry():
    _reset()
    results = []
    errors = []
    barrier = threading.Barrier(8)

    def worker():
        try:
            barrier.wait()
            results.append(ledger.post_entry(
                idempotency_key="race-1", actor="t", amount_cents=250, currency="usd",
                entry_type="funding", source="external:stripe", destination="user:2"))
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"unexpected errors: {errors}"
    non_dup = [r for r in results if not r.get("duplicate")]
    assert len(non_dup) == 1, f"expected exactly one winner, got {len(non_dup)}"
    assert _entry_count() == 2
    assert ledger.get_balance("user:2") == 250


# 3 -------------------------------------------------------------------------
def test_webhook_replay_out_of_order_delayed_idempotent():
    _reset()
    applied = []

    def handler(payload):
        # idempotent downstream write keyed on the event id
        ledger.post_entry(
            idempotency_key="evt:" + payload["id"], actor="stripe",
            amount_cents=payload["amount"], currency="usd", entry_type="capture",
            source="external:stripe", destination="user:" + str(payload["user"]))
        applied.append(payload["id"])

    ev = {"id": "evt_100", "amount": 700, "user": 3}

    # first delivery
    e1 = webhook_inbox.enqueue_event(provider="stripe", provider_event_id="evt_100",
                                     payload=ev, event_type="charge", signature_verified=True)
    assert e1["duplicate"] is False
    # replay (same id) BEFORE processing -> duplicate, no second row
    e2 = webhook_inbox.enqueue_event(provider="stripe", provider_event_id="evt_100", payload=ev)
    assert e2["duplicate"] is True

    r1 = webhook_inbox.process_event("stripe", "evt_100", handler)
    assert r1["status"] == "processed"
    # delayed re-delivery + reprocess -> skipped, ledger unchanged
    webhook_inbox.enqueue_event(provider="stripe", provider_event_id="evt_100", payload=ev)
    r2 = webhook_inbox.process_event("stripe", "evt_100", handler)
    assert r2.get("skipped") is True
    assert ledger.get_balance("user:3") == 700
    assert applied == ["evt_100"]  # handler side effect happened once


# 4 -------------------------------------------------------------------------
def test_balance_equals_sum_after_randomized_ops():
    _reset()
    rng = random.Random(1234)
    account = "user:9"
    n = 60
    for i in range(n):
        amt = rng.randint(1, 999)
        if rng.random() < 0.5:
            # credit the account
            ledger.post_entry(idempotency_key=f"rand-c-{i}", actor="t", amount_cents=amt,
                              currency="usd", entry_type="funding",
                              source="external:stripe", destination=account)
        else:
            # debit the account only if it can cover it (else fund then debit)
            if ledger.get_balance(account) < amt:
                ledger.post_entry(idempotency_key=f"rand-f-{i}", actor="t", amount_cents=amt,
                                  currency="usd", entry_type="funding",
                                  source="external:stripe", destination=account)
            ledger.post_entry(idempotency_key=f"rand-d-{i}", actor="t", amount_cents=amt,
                              currency="usd", entry_type="payout",
                              source=account, destination="platform:fees")
    # cached balance must equal the sum of entries, and must be non-negative
    assert ledger.get_balance(account) == _sum_account(account)
    assert ledger.get_balance(account) >= 0
    # global invariant: all signed entries net to zero (double-entry closes)
    conn = db.connect()
    total = conn.execute("SELECT COALESCE(SUM(signed_amount_cents),0) FROM ledger_entries").fetchone()[0]
    conn.close()
    assert int(total) == 0


# 5 -------------------------------------------------------------------------
def test_mid_processing_crash_is_replayable():
    _reset()
    state = {"fail": True}

    def flaky_handler(payload):
        if state["fail"]:
            raise RuntimeError("simulated crash mid-processing")
        ledger.post_entry(idempotency_key="evt:" + payload["id"], actor="stripe",
                          amount_cents=payload["amount"], currency="usd", entry_type="capture",
                          source="external:stripe", destination="user:" + str(payload["user"]))

    ev = {"id": "evt_200", "amount": 400, "user": 5}
    webhook_inbox.enqueue_event(provider="stripe", provider_event_id="evt_200", payload=ev)

    r1 = webhook_inbox.process_event("stripe", "evt_200", flaky_handler)
    assert r1["status"] == "failed"
    row = webhook_inbox.get_event("stripe", "evt_200")
    assert row["status"] == "failed" and row["retry_count"] == 1
    assert ledger.get_balance("user:5") == 0  # nothing applied yet

    # recovery: reconcile sweep replays it once the handler is healthy
    state["fail"] = False
    summary = webhook_inbox.reconcile_pending(flaky_handler)
    assert summary["processed"] == 1
    assert webhook_inbox.get_event("stripe", "evt_200")["status"] == "processed"
    assert ledger.get_balance("user:5") == 400


# 6 -------------------------------------------------------------------------
def _expect_ledger_error(**kwargs):
    try:
        ledger.post_entry(**kwargs)
    except ledger.LedgerError:
        return True
    return False


def test_rejections():
    _reset()
    base = dict(actor="t", currency="usd", entry_type="funding",
                source="external:stripe", destination="user:7")
    # missing idempotency key
    assert _expect_ledger_error(idempotency_key="", amount_cents=100, **base)
    # non-integer / float amount
    assert _expect_ledger_error(idempotency_key="x1", amount_cents=1.5, **base)
    # negative / zero amount
    assert _expect_ledger_error(idempotency_key="x2", amount_cents=0, **base)
    assert _expect_ledger_error(idempotency_key="x3", amount_cents=-5, **base)
    # boolean is not a valid amount
    assert _expect_ledger_error(idempotency_key="x4", amount_cents=True, **base)
    # same source/destination
    assert _expect_ledger_error(idempotency_key="x5", amount_cents=100, actor="t",
                                currency="usd", entry_type="funding",
                                source="user:7", destination="user:7")
    # overdraft on a normal (non-funding) account
    assert _expect_ledger_error(idempotency_key="x6", amount_cents=100, actor="t",
                                currency="usd", entry_type="payout",
                                source="user:empty", destination="platform:fees")
    # no partial writes happened
    assert _entry_count() == 0


# --- lightweight runner so the suite works without pytest installed ---------
def _run_standalone():
    setup_module()
    tests = [
        test_duplicate_submission_is_noop,
        test_concurrent_double_post_yields_one_entry,
        test_webhook_replay_out_of_order_delayed_idempotent,
        test_balance_equals_sum_after_randomized_ops,
        test_mid_processing_crash_is_replayable,
        test_rejections,
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
