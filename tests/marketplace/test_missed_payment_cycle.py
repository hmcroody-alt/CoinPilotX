"""The control plane around the missed-payment sweep.

``tests/marketplace/test_missed_payment_reconciliation.py`` covers what the
sweep decides about one transaction: that ``succeeded`` is the only thing it
acts on, that an outage is never recorded as "Stripe says unpaid", that a
repair writes an order and settles once. All of that was already true and
already unreachable — the sweep had no production caller, so none of it ran.

This file covers the half that decides whether it runs at all, how often, how
many rows at a time, what happens when two replicas wake up together, and —
the part with the most teeth — which rows get a slot in the batch.

That last one is not a tuning question. Candidates come back oldest-first under
a ``LIMIT``, so a handful of long-abandoned checkouts sitting at the head of the
queue occupy every slot of every batch forever, and a payment genuinely lost
today is never examined. A sweep in that state runs on schedule, reports nothing
wrong, and is blind to the single case it exists for. The starvation tests below
are the ones that would catch that, and it is worth noticing that every
individual assertion in the file next door would still pass while it happened.

The twelve provider/worker situations the brief names are each pinned here, in
provider-state terms: a Checkout Session's lifecycle shows up in this sweep as
the PaymentIntent status it drives, so "session open" is ``processing``, "session
expired" is ``canceled``, and "session complete" is ``succeeded``.
"""

import json
import os
import sqlite3
import sys
import tempfile
from contextlib import contextmanager
from datetime import datetime, timedelta

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

_HANDLE, _DB = tempfile.mkstemp(suffix=".db", prefix="missed_payment_cycle_")
os.close(_HANDLE)
os.environ["DATABASE_URL"] = f"sqlite:///{_DB}"
# Never a real key, and a test one on purpose: `_mutation_preconditions`
# refuses a key whose environment it cannot read, so an unset key here would
# block the repair path for a reason unrelated to what each test is pinning.
os.environ["STRIPE_SECRET_KEY"] = "sk_test_missed_payment_cycle_only"
os.environ["STRIPE_PUBLISHABLE_KEY"] = "pk_test_missed_payment_cycle_only"

import bot  # noqa: E402
from services import db as services_db  # noqa: E402
from services import marketplace_missed_payment_cycle as cycle  # noqa: E402

SELLER, BUYER = 99821, 99822
LONG_AGO = "2026-01-01T00:00:00"
NOW = datetime(2026, 6, 1, 12, 0, 0)


@pytest.fixture(scope="module", autouse=True)
def _app():
    bot.init_db()
    conn = sqlite3.connect(_DB)
    conn.execute(
        "INSERT INTO marketplace_sellers (user_id,status,display_name,created_at,updated_at) "
        "VALUES (?,'approved','Cycle Store',?,?)",
        (SELLER, LONG_AGO, LONG_AGO),
    )
    conn.commit()
    conn.close()
    cycle.ensure_schema()
    yield
    os.unlink(_DB)


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    """Clear the candidate table and the ledger between tests.

    The sweep selects every unsettled transaction in the database rather than a
    set it is handed, so a row one test deliberately leaves unsettled would be
    offered to the next test's fetcher — which raises on any intent it was not
    told about, on purpose, so without this the suite would fail in a way that
    looks like a bug in the sweep.

    Every switch is cleared too. These are read from the environment at call
    time, so a test that set one would otherwise decide the behaviour of every
    test after it, and the one that would break is whichever test pins the
    default.
    """
    conn = _db()
    conn.execute("DELETE FROM seller_transactions")
    conn.execute("DELETE FROM marketplace_orders")
    conn.execute(f"DELETE FROM {cycle.ATTEMPTS_TABLE}")
    conn.commit()
    conn.close()
    for name in (cycle.ENABLED_ENV_VAR, cycle.DRY_RUN_ENV_VAR, cycle.OWNER_AUTHORIZED_ENV_VAR,
                 cycle.INTERVAL_ENV_VAR, cycle.BATCH_ENV_VAR, cycle.GRACE_ENV_VAR,
                 cycle.RETRY_MAX_ATTEMPTS_ENV_VAR, cycle.RETRY_BASE_SECONDS_ENV_VAR,
                 cycle.RETRY_MAX_SECONDS_ENV_VAR):
        monkeypatch.delenv(name, raising=False)
    yield


def _db():
    conn = sqlite3.connect(_DB)
    conn.row_factory = sqlite3.Row
    return conn


_NEXT = [80000]


def _transaction(status="checkout_created", created_at=LONG_AGO, amount_cents=50):
    _NEXT[0] += 1
    tx_id = _NEXT[0]
    quote = {
        "quote_id": f"q{tx_id}", "fee_policy_version": "MARKETPLACE_LEGACY_CURRENT",
        "payout_policy_version": "MARKETPLACE_PAYOUTS_V1", "platform_fee_bps": 0,
        "merchandise_net_minor": amount_cents, "shipping_minor": 0, "tax_minor": 0,
        "seller_shipping_credit_minor": 0, "buyer_total_minor": amount_cents,
        "quantity": 1, "unit_price_minor": amount_cents,
    }
    conn = _db()
    conn.execute(
        "INSERT INTO seller_transactions (id,buyer_user_id,seller_user_id,seller_type,item_type,"
        "item_id,amount_cents,currency,platform_fee_cents,seller_net_cents,status,"
        "stripe_payment_intent_id,metadata_json,created_at,updated_at) "
        "VALUES (?,?,?,'merchant','marketplace_product',?,?,'USD',0,?,?,?,?,?,?)",
        (tx_id, BUYER, SELLER, 4242, amount_cents, amount_cents, status,
         f"pi_cycle_{tx_id}", json.dumps({"commercial_quote": quote}), created_at, created_at),
    )
    conn.commit()
    conn.close()
    return tx_id


def _intent(tx_id, status="succeeded"):
    return {
        "id": f"pi_cycle_{tx_id}", "status": status, "amount": 50, "amount_received": 50,
        "currency": "usd", "livemode": False,
        "metadata": {"cart_checkout": "1", "seller_transaction_ids": str(tx_id),
                     "buyer_user_id": str(BUYER)},
    }


def _fetcher(mapping):
    def fetch(intent_id):
        if intent_id not in mapping:
            raise AssertionError(f"the cycle fetched an intent it should not have: {intent_id}")
        value = mapping[intent_id]
        if isinstance(value, Exception):
            raise value
        return value
    return fetch


def _status(tx_id):
    conn = _db()
    try:
        row = conn.execute("SELECT status FROM seller_transactions WHERE id=?", (tx_id,)).fetchone()
        return str(row["status"]) if row else None
    finally:
        conn.close()


def _orders(tx_id):
    conn = _db()
    try:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM marketplace_orders WHERE seller_transaction_id=?", (tx_id,)).fetchall()]
    finally:
        conn.close()


def _ledger(tx_id):
    conn = _db()
    try:
        row = conn.execute(
            f"SELECT * FROM {cycle.ATTEMPTS_TABLE} WHERE seller_transaction_id=?",
            (tx_id,)).fetchone()
        return dict(row) if row else {}
    finally:
        conn.close()


def _enable(monkeypatch):
    monkeypatch.setenv(cycle.ENABLED_ENV_VAR, "true")


def _allow_repair(monkeypatch):
    """Grant permission to repair, without asserting anything about how it is granted.

    Two questions live in this module and they are tested separately on purpose:
    *when is repair permitted* (the gate ladder) and *what does the cycle do once
    it is*. This helper answers the first by fiat so the tests below can be about
    the second.

    It has to work that way here. The real ladder requires Postgres, and
    ``db.IS_POSTGRES`` cannot simply be patched true on this suite: ``services.db``
    reads it to decide whether to translate ``?`` placeholders into ``%s``, so
    flipping it points a Postgres dialect at a SQLite file and every query in the
    cycle fails. (The payout suite patches it and gets away with it only because
    those tests never execute SQL.) Patching the ladder's *answer* is the honest
    version of what patching ``IS_POSTGRES`` was pretending to do.

    The cost is that these tests cannot catch a weakened gate — so the gates get
    their own tests, each naming its exact reason string:
    ``test_repair_is_refused_off_postgres``,
    ``test_repair_is_refused_when_the_stripe_key_cannot_be_read`` and
    ``test_one_switch_on_its_own_does_not_authorise_a_repair``. Those call
    ``blocked_reason()``, which touches no database. Exact strings rather than
    "something was returned" because the ladder short-circuits on Postgres first:
    a test satisfied by any non-empty reason stays green with every Stripe gate
    beneath it deleted, which is a trap a Phase 5 mutation run caught in this
    repo rather than a hypothetical.

    ``leader_lock`` is patched to yield ``True`` only here;
    ``test_two_workers_overlapping_means_one_declines`` and
    ``test_repair_is_refused_off_postgres`` exercise the real one.
    """
    _enable(monkeypatch)
    monkeypatch.setenv(cycle.DRY_RUN_ENV_VAR, "false")
    monkeypatch.setenv(cycle.OWNER_AUTHORIZED_ENV_VAR, "true")
    monkeypatch.setattr(cycle, "_mutation_preconditions", lambda: "")

    @contextmanager
    def _leading():
        yield True

    monkeypatch.setattr(cycle, "leader_lock", _leading)


def _run(mapping, *, now=NOW):
    return cycle.run_cycle(fetch_payment_intent=_fetcher(mapping), now=now)


def _incidents(tx_id):
    from services.business_os.payments import incidents
    return [row for row in incidents.list_incidents(limit=300).get("incidents") or []
            if str(row.get("related_object") or "") == f"seller_transaction:{tx_id}"]


# --------------------------------------------------------------------------
# the switches, and which way each one fails
# --------------------------------------------------------------------------

def test_the_cycle_does_not_run_unless_it_is_switched_on():
    """Nothing happens on an unconfigured deployment, including no ledger writes."""
    tx_id = _transaction()
    assert cycle.cycle_enabled() is False
    assert cycle.run_missed_payment_cycle_if_due({}) is None
    assert _ledger(tx_id) == {}


def test_report_only_is_the_default_once_it_is_switched_on(monkeypatch):
    """Enabled detects. It does not repair until two more switches say so.

    This is the state the August incident needed and did not have: something
    watching, telling someone, and touching nothing.
    """
    _enable(monkeypatch)
    tx_id = _transaction()

    result = _run({f"pi_cycle_{tx_id}": _intent(tx_id)})

    assert cycle.blocked_reason() == "dry_run"
    assert result["repair_permitted"] is False
    assert result["detected_count"] == 1
    assert result["repaired_count"] == 0
    assert _status(tx_id) == "checkout_created"
    assert _orders(tx_id) == []
    # Detected and reported, which is the whole point of the default.
    assert len(_incidents(tx_id)) == 1


def test_one_switch_on_its_own_does_not_authorise_a_repair(monkeypatch):
    """Both mutation flags are required, and each names itself when it is the gap."""
    _enable(monkeypatch)
    monkeypatch.setenv(cycle.DRY_RUN_ENV_VAR, "false")
    monkeypatch.setattr(services_db, "IS_POSTGRES", True)
    assert cycle.blocked_reason() == "owner_not_authorized"

    monkeypatch.setenv(cycle.DRY_RUN_ENV_VAR, "true")
    monkeypatch.setenv(cycle.OWNER_AUTHORIZED_ENV_VAR, "true")
    assert cycle.blocked_reason() == "dry_run"


@pytest.mark.parametrize("raw", ["", "  ", "maybe", "TRUE-ish", "2"])
def test_an_unparseable_switch_never_becomes_the_reason_money_moved(monkeypatch, raw):
    """Every flag fails to its non-acting value, so a typo cannot authorise."""
    monkeypatch.setenv(cycle.ENABLED_ENV_VAR, raw)
    monkeypatch.setenv(cycle.DRY_RUN_ENV_VAR, raw)
    monkeypatch.setenv(cycle.OWNER_AUTHORIZED_ENV_VAR, raw)
    assert cycle.cycle_enabled() is False
    assert cycle.dry_run() is True
    assert cycle.owner_authorized() is False


def test_repair_is_refused_off_postgres(monkeypatch):
    """The real ``leader_lock`` cannot lock on SQLite, so repair is not allowed there.

    Asserted by exact reason, not by "something was returned". The ladder checks
    Postgres before it reads a Stripe key, so a test satisfied by any non-empty
    string would stay green with every Stripe gate below it deleted.
    """
    _enable(monkeypatch)
    monkeypatch.setenv(cycle.DRY_RUN_ENV_VAR, "false")
    monkeypatch.setenv(cycle.OWNER_AUTHORIZED_ENV_VAR, "true")
    monkeypatch.setattr(services_db, "IS_POSTGRES", False)

    assert cycle.blocked_reason() == "no_leader_lock_off_postgres"
    with cycle.leader_lock() as leading:
        assert leading is False


def test_repair_is_refused_when_the_stripe_key_cannot_be_read(monkeypatch):
    """A key whose environment is unreadable ranks with live, so repair waits."""
    _enable(monkeypatch)
    monkeypatch.setenv(cycle.DRY_RUN_ENV_VAR, "false")
    monkeypatch.setenv(cycle.OWNER_AUTHORIZED_ENV_VAR, "true")
    monkeypatch.setattr(services_db, "IS_POSTGRES", True)

    monkeypatch.setenv("STRIPE_SECRET_KEY", "rk_who_knows")
    assert cycle.blocked_reason() == "stripe_mode_unrecognized"

    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_x")
    monkeypatch.setenv("STRIPE_PUBLISHABLE_KEY", "pk_live_x")
    assert cycle.blocked_reason() == "stripe_mode_mixed"

    monkeypatch.delenv("STRIPE_SECRET_KEY")
    assert cycle.blocked_reason() == "stripe_not_configured"


# --------------------------------------------------------------------------
# cadence and bounded batches
# --------------------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("", cycle.DEFAULT_INTERVAL_SECONDS), ("nonsense", cycle.DEFAULT_INTERVAL_SECONDS),
    ("1", cycle.MIN_INTERVAL_SECONDS), ("-9999", cycle.MIN_INTERVAL_SECONDS),
    ("99999999", cycle.MAX_INTERVAL_SECONDS), ("1800", 1800),
])
def test_the_interval_is_clamped_so_a_typo_cannot_make_a_hot_loop(monkeypatch, raw, expected):
    monkeypatch.setenv(cycle.INTERVAL_ENV_VAR, raw)
    assert cycle.interval_seconds() == expected


@pytest.mark.parametrize("raw,expected", [
    ("", cycle.DEFAULT_BATCH), ("0", cycle.MIN_BATCH), ("-4", cycle.MIN_BATCH),
    ("100000", cycle.MAX_BATCH), ("40", 40),
])
def test_the_batch_is_clamped(monkeypatch, raw, expected):
    monkeypatch.setenv(cycle.BATCH_ENV_VAR, raw)
    assert cycle.batch_limit() == expected


def test_the_batch_bounds_how_many_rows_one_cycle_examines(monkeypatch):
    _enable(monkeypatch)
    monkeypatch.setenv(cycle.BATCH_ENV_VAR, "2")
    ids = [_transaction() for _ in range(5)]
    mapping = {f"pi_cycle_{tx}": _intent(tx) for tx in ids}

    result = _run(mapping)

    assert result["examined"] == 2
    assert cycle.batch_limit() == 2


def test_a_cycle_waits_out_its_interval_before_running_again(monkeypatch):
    """The deadline is monotonic, and it advances even when a cycle raises.

    A host loop's real period is its sleep plus however long the host took, so
    counting ticks would let a busy host stretch the interval silently. And a
    cycle that advanced its deadline only on success would retry a failing
    Stripe account on every single host tick.
    """
    _enable(monkeypatch)
    calls = []

    def _boom():
        calls.append(1)
        raise RuntimeError("stripe exploded")

    monkeypatch.setattr(cycle, "run_cycle", _boom)
    state: dict = {}

    first = cycle.run_missed_payment_cycle_if_due(state)
    assert first["status"] == "error"
    assert "missed_payment_cycle_due_at" in state

    assert cycle.run_missed_payment_cycle_if_due(state) is None
    assert len(calls) == 1


# --------------------------------------------------------------------------
# the twelve provider and worker situations
# --------------------------------------------------------------------------

def test_session_open_and_unpaid_keeps_its_budget(monkeypatch):
    """(1) An intent still in flight may yet succeed, so it is deferred, not retired."""
    _enable(monkeypatch)
    tx_id = _transaction()

    result = _run({f"pi_cycle_{tx_id}": _intent(tx_id, status="processing")})

    assert result["unpaid_count"] == 1
    assert result["retired_count"] == 0
    row = _ledger(tx_id)
    assert row["provider_status"] == "processing"
    assert row["exhausted_reason"] == ""
    assert row["next_attempt_at"] > cycle._iso(NOW)


def test_session_expired_and_unpaid_is_retired_on_sight(monkeypatch):
    """(2) ``canceled`` is the one status Stripe never revives.

    Retired immediately regardless of remaining budget. Polling it for the full
    eight-hour budget would be eight hours of a batch slot spent on an answer
    that cannot change — and that slot is the scarce resource here.
    """
    _enable(monkeypatch)
    tx_id = _transaction()

    result = _run({f"pi_cycle_{tx_id}": _intent(tx_id, status="canceled")})

    assert result["retired_count"] == 1
    row = _ledger(tx_id)
    assert row["attempts"] == 1
    assert row["exhausted_reason"] == cycle.EXHAUSTED_PROVIDER_CANCELED
    assert row["exhausted_at"] != ""
    assert tx_id in cycle.deferred_transaction_ids(NOW)


def test_session_complete_and_paid_is_repaired(monkeypatch):
    """(3)+(4) The incident itself: succeeded at Stripe, never recorded here."""
    _allow_repair(monkeypatch)
    tx_id = _transaction()

    result = _run({f"pi_cycle_{tx_id}": _intent(tx_id)})

    assert result["repair_permitted"] is True
    assert result["reason"] is None
    assert result["repaired_count"] == 1
    assert _status(tx_id) == "paid"
    orders = _orders(tx_id)
    assert len(orders) == 1
    assert orders[0]["status"] == "paid"
    # The ledger is cleared on repair, so a later audit of why a row was
    # deferred does not read as though it still is.
    assert _ledger(tx_id) == {}


def test_a_webhook_arriving_after_reconciliation_changes_nothing(monkeypatch):
    """(5) The late webhook settles an already-settled row, idempotently.

    Both triggers can fire for one intent and the order of arrival is not
    something this platform controls.
    """
    _allow_repair(monkeypatch)
    tx_id = _transaction()
    intent = _intent(tx_id)
    _run({intent["id"]: intent})

    assert len(_orders(tx_id)) == 1
    bot.pulse_settle_marketplace_payment_intent(intent, "", actor="stripe_webhook")

    assert _status(tx_id) == "paid"
    assert len(_orders(tx_id)) == 1


def test_reconciliation_running_twice_repairs_once(monkeypatch):
    """(6) The second cycle proves the candidate query excludes what it fixed.

    A sweep that kept re-examining settled rows would re-credit the seller on
    every tick, and the fetcher below raises if the row is offered to Stripe
    again — so this also pins that the row left the population rather than
    being re-fetched and harmlessly re-settled.
    """
    _allow_repair(monkeypatch)
    tx_id = _transaction()
    intent = _intent(tx_id)
    _run({intent["id"]: intent})

    second = _run({})

    assert second["examined"] == 0
    assert second["repaired_count"] == 0
    assert _status(tx_id) == "paid"
    assert len(_orders(tx_id)) == 1


def test_two_workers_overlapping_means_one_declines(monkeypatch):
    """(7) The loser of the lock does nothing and says why.

    Not an error: it will try again at its next deadline. The writes underneath
    are idempotent, so this is not what prevents a double order — it prevents
    the losing replica spending a cycle re-fetching intents and filing incidents
    the winner is already handling, which in the logs is indistinguishable from
    a provider in trouble.
    """
    _allow_repair(monkeypatch)

    @contextmanager
    def _already_held():
        yield False

    monkeypatch.setattr(cycle, "leader_lock", _already_held)
    tx_id = _transaction()

    # The fetcher is empty and raises on any call, so this also proves the
    # declining replica never reached Stripe at all.
    result = _run({})

    assert result["status"] == "skipped"
    assert result["reason"] == "not_leader"
    assert result["repaired_count"] == 0
    assert _status(tx_id) == "checkout_created"
    assert _ledger(tx_id) == {}


def test_stripe_being_unavailable_is_never_recorded_as_unpaid(monkeypatch):
    """(8) The distinction that keeps an outage from erasing a real payment.

    If unreachable collapsed into unpaid, an outage during a cycle would file
    every genuinely paid order as unpaid and the operator reading the summary
    would conclude there was nothing to repair.
    """
    _enable(monkeypatch)
    tx_id = _transaction()

    result = _run({f"pi_cycle_{tx_id}": RuntimeError("stripe is down")})

    assert result["unreachable_count"] == 1
    assert result["unpaid_count"] == 0
    assert result["repaired_count"] == 0
    assert _status(tx_id) == "checkout_created"
    assert _ledger(tx_id)["outcome"] == "unreachable"


def test_a_database_interruption_is_an_incident_for_this_cycle_not_the_host(monkeypatch):
    """(9) The host worker keeps its other jobs, and the deadline still advances.

    ``pulse_worker`` runs the feed engine, the reservation sweep, the release
    cycle and the payout cycle in the same loop. A database blip inside
    reconciliation must not take those down with it.
    """
    _enable(monkeypatch)

    def _db_down(**_kwargs):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(cycle, "run_cycle", _db_down)
    state: dict = {}

    outcome = cycle.run_missed_payment_cycle_if_due(state)

    assert outcome["status"] == "error"
    assert "database is locked" in outcome["error"]
    assert "missed_payment_cycle_due_at" in state
    assert state["missed_payment_cycle_last"]["last_cycle_status"] == "error"


def test_an_existing_order_is_not_duplicated(monkeypatch):
    """(10) Projection is keyed on the transaction, so a second pass adds nothing."""
    _allow_repair(monkeypatch)
    tx_id = _transaction()
    intent = _intent(tx_id)
    _run({intent["id"]: intent})
    first = _orders(tx_id)
    assert len(first) == 1

    # Settle the same intent again by both routes available to it.
    bot.pulse_settle_marketplace_payment_intent(intent, "", actor="reconciliation_sweep")
    bot.pulse_settle_marketplace_payment_intent(intent, "", actor="stripe_webhook")

    after = _orders(tx_id)
    assert len(after) == 1
    assert after[0]["id"] == first[0]["id"]


def test_a_transaction_already_paid_is_never_examined(monkeypatch):
    """(11) Excluded by the candidate query, so Stripe is not even asked."""
    _enable(monkeypatch)
    tx_id = _transaction(status="paid")

    result = _run({})  # raises if the row is offered to Stripe

    assert result["examined"] == 0
    assert _status(tx_id) == "paid"


def test_a_refunded_transaction_is_never_walked_back_to_paid(monkeypatch):
    """(12) A refund outranks the original success, and the intent stays succeeded.

    Stripe answers ``succeeded`` for a refunded charge's intent forever, so if
    this row reached the sweep it would be "repaired" back to paid. The
    candidate query is what stops that, which is why the fetcher here is empty.
    """
    _enable(monkeypatch)
    tx_id = _transaction(status="refunded")

    result = _run({})

    assert result["examined"] == 0
    assert _status(tx_id) == "refunded"


def test_a_checkout_inside_the_grace_window_is_left_alone(monkeypatch):
    """Stripe retries a failing endpoint for hours; the cycle must not race it.

    Without the window every checkout in progress would be reported as a lost
    payment, and the alarm this exists to raise would be buried in false
    positives from its first tick.
    """
    _enable(monkeypatch)
    tx_id = _transaction(created_at="2099-01-01T00:00:00")

    result = _run({})

    assert result["examined"] == 0
    assert _ledger(tx_id) == {}


# --------------------------------------------------------------------------
# backoff, and the starvation it exists to prevent
# --------------------------------------------------------------------------

def test_backoff_doubles_from_the_base_and_is_capped():
    policy = {"max_attempts": 9, "base_seconds": 900, "max_seconds": 21600}
    assert [cycle.backoff_seconds(n, policy) for n in range(1, 7)] == \
        [900, 1800, 3600, 7200, 14400, 21600]
    # Capped, and the exponent is clamped before the shift rather than after:
    # computing 2**900 only to discard it in min() is how a retry becomes a hang.
    assert cycle.backoff_seconds(900, policy) == 21600


@pytest.mark.parametrize("raw,expected", [
    ("", cycle.DEFAULT_RETRY_MAX_ATTEMPTS), ("0", cycle.MIN_RETRY_MAX_ATTEMPTS),
    ("9999", cycle.MAX_RETRY_MAX_ATTEMPTS), ("junk", cycle.DEFAULT_RETRY_MAX_ATTEMPTS),
])
def test_the_retry_budget_is_clamped(monkeypatch, raw, expected):
    monkeypatch.setenv(cycle.RETRY_MAX_ATTEMPTS_ENV_VAR, raw)
    assert cycle.retry_policy()["max_attempts"] == expected


def test_a_deferred_row_does_not_spend_a_batch_slot(monkeypatch):
    """A row waiting out its backoff is excluded, so the next one gets the slot."""
    _enable(monkeypatch)
    monkeypatch.setenv(cycle.BATCH_ENV_VAR, "1")
    first, second = _transaction(), _transaction()

    one = _run({f"pi_cycle_{first}": _intent(first, status="processing")})
    assert one["examined"] == 1

    # Same instant, so `first` is still inside its backoff: the batch of one
    # must go to `second` rather than to `first` again.
    two = _run({f"pi_cycle_{second}": _intent(second, status="processing")})
    assert two["examined"] == 1
    assert two["deferred_count"] == 1


def test_old_abandoned_checkouts_cannot_starve_a_payment_lost_today(monkeypatch):
    """The defect that makes a scheduled sweep useless while looking healthy.

    Candidates come back oldest-first under a ``LIMIT``. Here three ancient
    abandoned checkouts fill a batch of three, and a real lost payment is newer
    than all of them. Without deferral the cycle would examine the same three
    dead rows on every tick for the life of the deployment and never reach the
    fourth — running on schedule, reporting nothing wrong, blind to the one case
    it exists for.

    Note what the first cycle proves on its own: nothing. It is green, it
    examined three rows, it found no missed payment, and that is precisely the
    state the bug produces. Only the second cycle can tell the difference.
    """
    _allow_repair(monkeypatch)
    monkeypatch.setenv(cycle.BATCH_ENV_VAR, "3")
    abandoned = [_transaction(created_at=f"2026-01-0{n}T00:00:00") for n in (1, 2, 3)]
    lost = _transaction(created_at="2026-05-01T00:00:00")

    first = _run({f"pi_cycle_{tx}": _intent(tx, status="canceled") for tx in abandoned})
    assert first["examined"] == 3
    assert first["repaired_count"] == 0

    second = _run({f"pi_cycle_{lost}": _intent(lost)})

    assert second["deferred_count"] == 3
    assert second["examined"] == 1
    assert second["repaired_count"] == 1
    assert _status(lost) == "paid"


def test_a_row_stops_being_asked_about_once_its_budget_runs_out(monkeypatch):
    """An unbounded retry is not persistence, it is a way of never telling anyone."""
    _enable(monkeypatch)
    monkeypatch.setenv(cycle.RETRY_MAX_ATTEMPTS_ENV_VAR, "3")
    tx_id = _transaction()
    mapping = {f"pi_cycle_{tx_id}": _intent(tx_id, status="processing")}

    moment = NOW
    for attempt in range(1, 4):
        result = _run(mapping, now=moment)
        assert result["examined"] == 1, f"attempt {attempt} was deferred when it was due"
        row = _ledger(tx_id)
        assert row["attempts"] == attempt
        # Jump past the backoff so the next cycle finds the row due.
        moment = moment + timedelta(seconds=cycle.backoff_seconds(attempt, cycle.retry_policy()) + 60)

    assert _ledger(tx_id)["exhausted_reason"] == cycle.EXHAUSTED_ABANDONED_UNPAID
    # Due by the clock, retired by the ledger: excluded from here on.
    assert _run({}, now=moment)["examined"] == 0


def test_giving_up_on_an_unreachable_provider_opens_an_incident(monkeypatch):
    """The one exhaustion that is news, and the August failure in miniature.

    An abandoned checkout running out of budget is the normal end of its life.
    A row whose provider was never reachable means this server still does not
    know whether that money moved — so it must not leave the queue quietly.
    """
    _enable(monkeypatch)
    monkeypatch.setenv(cycle.RETRY_MAX_ATTEMPTS_ENV_VAR, "2")
    tx_id = _transaction()
    mapping = {f"pi_cycle_{tx_id}": RuntimeError("stripe is down")}

    first = _run(mapping, now=NOW)
    assert first["retired_count"] == 0
    assert _incidents(tx_id) == []

    later = NOW + timedelta(seconds=cycle.backoff_seconds(1, cycle.retry_policy()) + 60)
    second = _run(mapping, now=later)

    assert second["retired_count"] == 1
    assert _ledger(tx_id)["exhausted_reason"] == cycle.EXHAUSTED_PROVIDER_UNREACHABLE
    filed = _incidents(tx_id)
    assert len(filed) == 1
    assert filed[0]["severity"] == "critical"


def test_an_unrecognised_metadata_shape_is_retired_for_a_human_not_retried(monkeypatch):
    """Asking Stripe again returns the same answer, so retrying is the wrong tool.

    The singular and ad-funding checkout shapes settle through different webhook
    branches. Pushing an unrecognised one through the single settlement this
    sweep knows would credit the wrong party, and this is real money.
    """
    _enable(monkeypatch)
    tx_id = _transaction()
    intent = dict(_intent(tx_id), metadata={"purpose": "pulse_ad_wallet_funding"})

    result = _run({intent["id"]: intent})

    assert result["needs_attention_count"] == 1
    assert result["retired_count"] == 1
    assert _ledger(tx_id)["exhausted_reason"] == cycle.EXHAUSTED_NEEDS_HUMAN
    assert _status(tx_id) == "checkout_created"
    assert _orders(tx_id) == []


def test_a_detected_but_unrepaired_row_is_not_refiled_every_cycle(monkeypatch):
    """Report-only must not mean one fact repeated into the incident queue forever.

    A cycle every fifteen minutes that refiled on each pass would bury every
    other incident under the same row within a day.
    """
    _enable(monkeypatch)
    tx_id = _transaction()
    mapping = {f"pi_cycle_{tx_id}": _intent(tx_id)}

    first = _run(mapping)
    assert first["detected_count"] == 1
    assert first["awaiting_repair_count"] == 1

    second = _run({})  # raises if the row is offered to Stripe again

    assert second["examined"] == 0
    assert len(_incidents(tx_id)) == 1


# --------------------------------------------------------------------------
# what an operator can see
# --------------------------------------------------------------------------

def test_the_heartbeat_says_enabled_and_separately_says_repairing(monkeypatch):
    """The two are not the same, and conflating them is the reportable failure.

    Railway variables are per service. An owner who sets the switches on the web
    service and leaves this worker untouched gets a cycle that never repairs;
    printing both fields is what puts that in the heartbeat rather than in a
    month of nobody noticing.
    """
    assert cycle.heartbeat_metadata({}) == {"missed_payment_cycle_enabled": False}

    _enable(monkeypatch)
    reporting = cycle.heartbeat_metadata({})
    assert reporting["missed_payment_cycle_enabled"] is True
    assert reporting["missed_payment_cycle_may_repair"] is False
    assert reporting["missed_payment_cycle_blocked_by"] == "dry_run"
    assert reporting["missed_payment_cycle_stripe_mode"] == "test"

    _allow_repair(monkeypatch)
    repairing = cycle.heartbeat_metadata({})
    assert repairing["missed_payment_cycle_may_repair"] is True
    assert repairing["missed_payment_cycle_blocked_by"] is None


def test_the_heartbeat_distinguishes_detected_from_repaired(monkeypatch):
    """A report-only deployment shows the first rising and the second flat.

    That shape is the whole operational signal: payments are being lost and
    nobody has authorised fixing them. A single "reconciled" counter would make
    it unreadable.
    """
    _enable(monkeypatch)
    tx_id = _transaction()
    # Bound before the patch: `_run` goes through `cycle.run_cycle`, so a lambda
    # that called it after patching would call itself.
    real_run_cycle = cycle.run_cycle
    monkeypatch.setattr(
        cycle, "run_cycle",
        lambda **_kwargs: real_run_cycle(
            fetch_payment_intent=_fetcher({f"pi_cycle_{tx_id}": _intent(tx_id)}), now=NOW))
    state: dict = {}

    cycle.run_missed_payment_cycle_if_due(state)
    metadata = cycle.heartbeat_metadata(state)

    assert metadata["last_cycle_detected"] == 1
    assert metadata["last_cycle_repaired"] == 0
    assert metadata["last_cycle_status"] == "ok"
    assert metadata["last_cycle_reason"] == "dry_run"


def test_the_heartbeat_survives_a_tick_on_which_the_cycle_did_not_run(monkeypatch):
    """``record_worker_heartbeat`` replaces metadata wholesale.

    This cycle runs on roughly one host tick in forty-five, so reporting only
    the current tick would blank ``last_cycle_at`` in between — which reads
    identically to a cycle that never ran, the exact ambiguity this module
    exists to remove.
    """
    _enable(monkeypatch)
    state = {"missed_payment_cycle_last": {"last_cycle_at": "2026-06-01T12:00:00",
                                           "last_cycle_detected": 3}}

    metadata = cycle.heartbeat_metadata(state)

    assert metadata["last_cycle_at"] == "2026-06-01T12:00:00"
    assert metadata["last_cycle_detected"] == 3


def test_the_cycle_is_wired_into_the_worker_that_actually_runs_in_production():
    """The defect being closed was an uncalled function, so the call site is pinned.

    ``pulse_worker.py`` has no Procfile line — it runs because the Railway
    service ``coinpilotx-pulse-worker`` overrides its start command. That is
    easy to miss and easy to "clean up", and the sweep having no caller is
    precisely the condition that let a real payment go missing for 37 days.
    """
    import pulse_worker

    source = open(pulse_worker.__file__, encoding="utf-8").read()
    assert "missed_payment_cycle.run_missed_payment_cycle_if_due(state)" in source
    assert "missed_payment_cycle.heartbeat_metadata(state)" in source


def test_every_switch_is_declared_in_the_env_contract():
    """An undeclared variable is one an owner cannot discover."""
    contract = open(os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        ".env.example"), encoding="utf-8").read()
    for name in (cycle.ENABLED_ENV_VAR, cycle.DRY_RUN_ENV_VAR, cycle.OWNER_AUTHORIZED_ENV_VAR,
                 cycle.INTERVAL_ENV_VAR, cycle.BATCH_ENV_VAR, cycle.GRACE_ENV_VAR,
                 cycle.RETRY_MAX_ATTEMPTS_ENV_VAR, cycle.RETRY_BASE_SECONDS_ENV_VAR,
                 cycle.RETRY_MAX_SECONDS_ENV_VAR):
        assert f"\n{name}=" in contract, f"{name} is not declared in .env.example"


def test_the_advisory_lock_key_is_not_shared_with_another_cycle():
    """Two cycles on one key would silently exclude each other.

    Both are correct to run concurrently, so sharing a key would not corrupt
    anything — it would just make one of them skip intermittently, which reads
    as flakiness rather than as a lock.
    """
    from services import marketplace_payout_worker as payout

    assert cycle.ADVISORY_LOCK_KEY != payout.ADVISORY_LOCK_KEY
    assert cycle.ADVISORY_LOCK_KEY != 620260524  # bot.init_db's migration lock
