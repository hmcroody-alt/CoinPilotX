"""The payout worker is the caller that turns real money movement on.

`marketplace_payout_scheduler.run_once` moves a seller's cleared earnings out of
the platform balance — a Stripe transfer to their connected account, then a
payout to their bank. Neither leg can be taken back by this platform; money can
only be *requested* back from a seller. Until now it deliberately had no caller
at all, and "no caller" was the safety property.

This module replaces that property with a weaker but usable one: a caller exists,
and it is off. Everything here exists to prove the off-ness is real rather than
documented, because the failure mode is silent and expensive — a worker that pays
out when nobody meant it to looks exactly like a worker doing its job.

The tests that matter most are the ones asserting a *negative*: that the
scheduler is never reached. A negative is easy to satisfy vacuously, so each one
is paired with a positive control that opens the gates and proves the same call
path does reach the scheduler.
"""

import os
import pathlib
import sys
import tempfile

os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(
    tempfile.mkdtemp(prefix="mkt_payout_worker_"), "test.db")
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

import json  # noqa: E402
from datetime import datetime, timedelta, timezone  # noqa: E402

import pytest  # noqa: E402

from services import db  # noqa: E402
from services import marketplace_payout_worker as worker  # noqa: E402
from services import marketplace_payout_scheduler as scheduler  # noqa: E402
from services import marketplace_settlement_service as settlements  # noqa: E402
from services import stripe_mode  # noqa: E402
from services.business_os.ledger import ledger  # noqa: E402

ALL_FLAGS = (worker.ENABLED_ENV_VAR, worker.DRY_RUN_ENV_VAR,
             worker.OWNER_AUTHORIZED_ENV_VAR, worker.INTERVAL_ENV_VAR,
             worker.BATCH_ENV_VAR,
             # Not a switch the owner throws, but it is now one of the things
             # standing between this worker and a transfer, so a test that
             # inherits a developer's real key is a test proving nothing.
             stripe_mode.SECRET_KEY_ENV_VAR)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Start every test from an unconfigured environment.

    Without this a flag set by one test would leak into the next, and the tests
    asserting "off by default" are exactly the ones that would pass anyway.
    """
    for name in ALL_FLAGS:
        monkeypatch.delenv(name, raising=False)


def _eligible_settlement(tx_id: int, seller_id: int, net_minor: int = 9000) -> None:
    """A settlement that has cleared delivery and its protection window."""
    ledger.ensure_schema()
    settlements.ensure_schema()
    tx = {"id": tx_id, "seller_user_id": seller_id, "item_type": "marketplace_product",
          "amount_cents": net_minor + 1000, "platform_fee_cents": 1000,
          "seller_net_cents": net_minor, "currency": "USD",
          "metadata_json": json.dumps({"commercial_quote": {
              "merchandise_net_minor": net_minor + 1000,
              "buyer_total_minor": net_minor + 1000,
              "platform_fee_bps": 1000,
              "fee_policy_version": "MARKETPLACE_LEGACY_CURRENT"}})}
    settlements.settle_paid_transaction(tx, payout_ready=True)
    settlements.mark_delivered(tx_id, actor="carrier", idempotency_key=f"delivered:{tx_id}")
    settlements.evaluate_eligibility(tx_id, now=datetime.now(timezone.utc) + timedelta(days=3))


def _open_every_gate(monkeypatch):
    """Both mutation switches plus the two deployment preconditions.

    A test key, not a live one. The precondition it satisfies is "the deployment
    can say which Stripe this is", and a test key says so. Nothing in this file
    reaches a provider, so the value only has to be classifiable.
    """
    monkeypatch.setenv(worker.ENABLED_ENV_VAR, "true")
    monkeypatch.setenv(worker.DRY_RUN_ENV_VAR, "false")
    monkeypatch.setenv(worker.OWNER_AUTHORIZED_ENV_VAR, "true")
    monkeypatch.setenv(stripe_mode.SECRET_KEY_ENV_VAR, "sk_test_payout_worker_suite")
    monkeypatch.setattr(db, "IS_POSTGRES", True)


# ---------------------------------------------------------------------------
# The flags
# ---------------------------------------------------------------------------

def test_the_worker_is_off_in_a_default_environment():
    """The whole point of the module. If this ever fails, nothing else matters."""
    assert worker.worker_enabled() is False
    assert worker.dry_run() is True
    assert worker.owner_authorized() is False
    assert worker.may_move_money() is False


@pytest.mark.parametrize("value", ["", "   ", "maybe", "TRUE-ish", "2", "yes please", "null"])
def test_every_flag_fails_closed_on_a_value_it_cannot_parse(monkeypatch, value):
    """An unparseable flag must never be the reason money moved.

    `"TRUE-ish"` and `"yes please"` are in here on purpose: both contain a
    truthy word, and a parser written with `in` or `startswith` would accept
    them.
    """
    for name in (worker.ENABLED_ENV_VAR, worker.OWNER_AUTHORIZED_ENV_VAR):
        monkeypatch.setenv(name, value)
    monkeypatch.setenv(worker.DRY_RUN_ENV_VAR, value)
    assert worker.worker_enabled() is False
    assert worker.owner_authorized() is False
    # Dry run is the flag whose safe direction is *True*, so it fails the other
    # way and the assertion has to be written the other way too.
    assert worker.dry_run() is True
    assert worker.may_move_money() is False


@pytest.mark.parametrize("dry,authorized,expected", [
    ("true", "true", False),    # dry run wins even when authorized
    ("false", "false", False),  # not authorized wins even when not dry
    ("false", "true", True),    # the only combination that moves money
    ("true", "false", False),
])
def test_mutation_needs_both_switches_and_not_either(monkeypatch, dry, authorized, expected):
    """Two independent gates, not one gate checked twice.

    The `("false", "false")` row is the one that catches an implementation that
    only reads `DRY_RUN`, and `("true", "true")` catches one that only reads
    `OWNER_AUTHORIZED`.
    """
    monkeypatch.setenv(worker.DRY_RUN_ENV_VAR, dry)
    monkeypatch.setenv(worker.OWNER_AUTHORIZED_ENV_VAR, authorized)
    assert worker.may_move_money() is expected


def test_the_interval_and_batch_cannot_be_typed_into_a_hot_loop(monkeypatch):
    monkeypatch.setenv(worker.INTERVAL_ENV_VAR, "0")
    assert worker.interval_seconds() == worker.MIN_INTERVAL_SECONDS
    monkeypatch.setenv(worker.INTERVAL_ENV_VAR, "999999")
    assert worker.interval_seconds() == worker.MAX_INTERVAL_SECONDS
    monkeypatch.setenv(worker.INTERVAL_ENV_VAR, "not a number")
    assert worker.interval_seconds() == worker.DEFAULT_INTERVAL_SECONDS

    monkeypatch.setenv(worker.BATCH_ENV_VAR, "-5")
    assert worker.batch_limit() == worker.MIN_BATCH
    monkeypatch.setenv(worker.BATCH_ENV_VAR, "100000")
    assert worker.batch_limit() == worker.MAX_BATCH


# ---------------------------------------------------------------------------
# The scheduler must not be reached
# ---------------------------------------------------------------------------

def test_a_default_environment_never_reaches_the_scheduler(monkeypatch):
    """The negative, and the positive control that proves it is not vacuous."""
    calls = []
    monkeypatch.setattr(scheduler, "run_once", lambda **kw: calls.append(kw) or {})

    outcome = worker.run_cycle()
    assert calls == []
    assert outcome["moved_money"] is False
    assert outcome["status"] == "preview"
    assert outcome["reason"] == "dry_run"

    # Positive control: the same call, with the gates open, does reach it.
    _open_every_gate(monkeypatch)
    monkeypatch.setattr(worker, "leader_lock", _fake_lock(True))
    worker.run_cycle()
    assert len(calls) == 1, "the negative above proves nothing if this path is also dead"


def test_being_authorized_is_not_enough_while_still_in_dry_run(monkeypatch):
    calls = []
    monkeypatch.setattr(scheduler, "run_once", lambda **kw: calls.append(kw) or {})
    monkeypatch.setenv(worker.OWNER_AUTHORIZED_ENV_VAR, "true")
    # Deliberately not faking `IS_POSTGRES` here. This cycle falls through to the
    # read-only preview, which touches the database, and `db._translate_sql`
    # rewrites `?` to `%s` whenever that flag is set - so faking it in a test that
    # then runs a query produces a SQLite syntax error rather than the behaviour
    # under test. The gate being asserted is `dry_run`, which is checked first
    # regardless of engine.

    outcome = worker.run_cycle()
    assert calls == []
    assert outcome["reason"] == "dry_run"


def test_money_cannot_move_off_postgres_even_with_both_switches_open(monkeypatch):
    """SQLite has no cluster to coordinate, so there is no leader lock to hold.

    Yielding `True` from a lock that locks nothing would be worse than refusing:
    it would read as protected in every log line.
    """
    calls = []
    monkeypatch.setattr(scheduler, "run_once", lambda **kw: calls.append(kw) or {})
    monkeypatch.setenv(worker.DRY_RUN_ENV_VAR, "false")
    monkeypatch.setenv(worker.OWNER_AUTHORIZED_ENV_VAR, "true")
    monkeypatch.setattr(db, "IS_POSTGRES", False)

    outcome = worker.run_cycle()
    assert calls == []
    assert outcome["reason"] == "no_leader_lock_off_postgres"


def test_the_leader_lock_declines_rather_than_pretending_on_sqlite(monkeypatch):
    monkeypatch.setattr(db, "IS_POSTGRES", False)
    with worker.leader_lock() as leading:
        assert leading is False


def test_a_replica_that_loses_the_lock_leaves_the_rows_alone(monkeypatch):
    """The losing replica must not generate failures on rows the winner has.

    Without this it would run the same batch, collide on the idempotency keys and
    write `failed` transitions that are indistinguishable from a Stripe outage.
    """
    calls = []
    monkeypatch.setattr(scheduler, "run_once", lambda **kw: calls.append(kw) or {})
    _open_every_gate(monkeypatch)
    monkeypatch.setattr(worker, "leader_lock", _fake_lock(False))

    outcome = worker.run_cycle()
    assert calls == []
    assert outcome == {"status": "skipped", "moved_money": False, "reason": "not_leader"}


def _fake_lock(acquired: bool):
    from contextlib import contextmanager

    @contextmanager
    def _lock():
        yield acquired
    return _lock


class _RecordingConn:
    """A connection that records SQL instead of running it.

    There is no Postgres available in this environment (the local VM does not
    boot), so the advisory-lock path below cannot be exercised against a real
    server. That limits what these two tests can claim: they prove the *contract*
    — which statements are issued, and that the lock is released and the
    connection closed on every exit — and they do not prove Postgres's own
    advisory-lock semantics. The release discipline is the half that would break
    silently, so it is the half worth pinning here.
    """

    def __init__(self, locked: bool):
        self.locked = locked
        self.statements: list[str] = []
        self.closed = False
        self.committed = 0

    def execute(self, sql, params=()):
        self.statements.append(" ".join(str(sql).split()))
        conn = self

        class _Result:
            def fetchone(self):
                return {"locked": conn.locked}
        return _Result()

    def commit(self):
        self.committed += 1

    def close(self):
        self.closed = True


@pytest.mark.parametrize("row,expected", [
    ({"locked": True}, True),
    ({"locked": False}, False),
    (None, False),
    ((True,), True),      # a bare DBAPI tuple: no .get, no string key
    ((False,), False),
    ([True], True),
])
def test_the_lock_result_is_read_from_every_row_shape_this_repo_produces(row, expected):
    """`db.CompatRow` is a Mapping, `sqlite3.Row` takes key or index, and a bare
    cursor yields a tuple with neither.

    A reader that assumed one shape would raise inside the lock, and that
    exception is caught by the cycle's own handler — so the failure would surface
    as "provider down" rather than "the lock reader is wrong", and the payout
    system would look broken for a reason nothing pointed at.
    """
    assert worker._row_value(row) is expected


def test_the_leader_lock_asks_postgres_and_never_waits(monkeypatch):
    """`pg_try_advisory_lock`, not `pg_advisory_lock`.

    The blocking form would make a losing replica queue up behind the cycle that
    is already doing the work it wanted to do, and then run a redundant batch the
    moment it was granted.
    """
    conn = _RecordingConn(locked=True)
    monkeypatch.setattr(db, "IS_POSTGRES", True)
    monkeypatch.setattr(db, "connect", lambda: conn)

    with worker.leader_lock() as leading:
        assert leading is True

    assert any("pg_try_advisory_lock" in s for s in conn.statements)
    assert not any("pg_advisory_lock(" in s for s in conn.statements), \
        "the blocking form would queue replicas instead of declining"
    assert any("pg_advisory_unlock" in s for s in conn.statements), \
        "a session-scoped lock left held would block every later cycle"
    assert conn.closed is True


def test_a_failing_cycle_still_releases_the_leader_lock(monkeypatch):
    """The lock is session-scoped, so an exception that escaped without
    unlocking would leave every subsequent cycle unable to acquire it — the
    payout system would stop, quietly, until the connection happened to drop."""
    conn = _RecordingConn(locked=True)
    monkeypatch.setattr(db, "IS_POSTGRES", True)
    monkeypatch.setattr(db, "connect", lambda: conn)

    with pytest.raises(RuntimeError):
        with worker.leader_lock() as leading:
            assert leading is True
            raise RuntimeError("the cycle blew up")

    assert any("pg_advisory_unlock" in s for s in conn.statements)
    assert conn.closed is True


def test_a_lock_that_was_not_acquired_is_not_unlocked(monkeypatch):
    """Unlocking a lock this session never held is a no-op in Postgres, but it
    logs a warning on the server and would make the logs lie about which replica
    was leading."""
    conn = _RecordingConn(locked=False)
    monkeypatch.setattr(db, "IS_POSTGRES", True)
    monkeypatch.setattr(db, "connect", lambda: conn)

    with worker.leader_lock() as leading:
        assert leading is False

    assert not any("pg_advisory_unlock" in s for s in conn.statements)
    assert conn.closed is True


# ---------------------------------------------------------------------------
# The preview
# ---------------------------------------------------------------------------

def test_the_preview_writes_nothing_at_all(monkeypatch):
    """Dry run cannot be "run the scheduler with recording stubs".

    `run_once` creates a canonical payout request and transitions the settlement
    to `scheduled` *before* it ever calls a provider. A dry run built that way
    would leave real state behind while reporting that it changed nothing.
    """
    _eligible_settlement(4101, seller_id=71)
    before = dict(settlements.get_settlement(4101))

    result = worker.preview()

    assert result["would_pay_count"] >= 1
    assert dict(settlements.get_settlement(4101)) == before


def test_the_preview_counts_the_same_rows_the_scheduler_would_take():
    """A preview that reports a different population than the cycle it previews
    is worse than no preview: it is the number an operator reads before deciding
    to open the money gates."""
    _eligible_settlement(4102, seller_id=72, net_minor=5500)
    previewed = worker.preview()

    calls = []
    metrics = scheduler.run_once(
        account_resolver=lambda _: {"connected_account_id": "acct_x", "payouts_enabled": True},
        provider_transfer=lambda a: calls.append(a) or {"id": "tr_x"},
        provider_create=lambda a: calls.append(a) or {"id": "po_x"},
        limit=worker.batch_limit())

    assert previewed["would_pay_count"] == metrics["eligible_count"]


def test_a_blocked_settlement_is_absent_from_the_preview():
    """`blocker_code` is how a chargeback, a fraud warning and a deauthorization
    all freeze a payout. If the preview ignored it, the headline number an
    operator approves would include money that must not move."""
    _eligible_settlement(4103, seller_id=73, net_minor=7700)
    with_blocker = worker.preview()["would_pay_count"]

    conn = db.connect()
    try:
        conn.execute("UPDATE marketplace_commercial_settlements SET blocker_code='dispute' "
                     "WHERE seller_transaction_id=?", (4103,))
        conn.commit()
    finally:
        conn.close()

    assert worker.preview()["would_pay_count"] == with_blocker - 1


# ---------------------------------------------------------------------------
# The provider adapters
# ---------------------------------------------------------------------------

def test_the_transfer_adapter_forwards_the_idempotency_key(monkeypatch):
    """The stable key is the whole reason a retry cannot pay twice.

    `build_stripe_transfer_args` derives `seller_transfer:<payout_key>`, but it
    only *shapes* the argument — an adapter that dropped it on the floor would
    still transfer successfully, and would create a second transfer on every
    retry. Nothing else in the system would notice.
    """
    from services import payment_provider

    seen = {}

    def fake_create_transfer(**kwargs):
        seen.update(kwargs)
        return {"ok": True, "provider_transfer_id": "tr_live"}

    monkeypatch.setattr(payment_provider, "create_transfer", fake_create_transfer)

    out = worker._transfer_via_stripe({
        "idempotency_key": "seller_transfer:marketplace:payout:9",
        "kwargs": {"amount": 4500, "currency": "usd", "destination": "acct_seller"},
    })

    assert out == {"id": "tr_live"}
    assert seen["idempotency_key"] == "seller_transfer:marketplace:payout:9"
    assert seen["destination"] == "acct_seller"
    # A transfer leaves the *platform* balance. `stripe_account` here would
    # execute the call as the connected account, which is a different movement.
    assert "stripe_account" not in seen


def test_the_payout_adapter_executes_as_the_connected_account(monkeypatch):
    """The mirror image: `stripe_account` is required on a payout and its
    idempotency key must differ from the transfer's, or the second leg would
    dedupe against the first."""
    from services import payment_provider

    seen = {}

    def fake_create_payout(**kwargs):
        seen.update(kwargs)
        return {"ok": True, "provider_payout_id": "po_live"}

    monkeypatch.setattr(payment_provider, "create_payout", fake_create_payout)

    out = worker._payout_via_stripe({
        "stripe_account": "acct_seller",
        "idempotency_key": "seller_payout:marketplace:payout:9",
        "kwargs": {"amount": 4500, "currency": "usd"},
    })

    assert out == {"id": "po_live"}
    assert seen["stripe_account"] == "acct_seller"
    assert seen["idempotency_key"] == "seller_payout:marketplace:payout:9"


@pytest.mark.parametrize("adapter,provider_name,args", [
    ("_transfer_via_stripe", "create_transfer",
     {"idempotency_key": "k", "kwargs": {"amount": 1, "destination": "acct"}}),
    ("_payout_via_stripe", "create_payout",
     {"stripe_account": "acct", "idempotency_key": "k", "kwargs": {"amount": 1}}),
])
def test_a_refused_provider_call_raises_with_the_real_reason(monkeypatch, adapter,
                                                             provider_name, args):
    """`create_transfer` returns `{"ok": False}` rather than raising when Stripe
    is unconfigured.

    Passing that through would hand `run_once` `{"id": None}`, which it reports
    as "provider returned no transfer id" — true, useless, and identical to a
    genuine provider fault. The adapter raises the actual message instead.
    """
    from services import payment_provider

    monkeypatch.setattr(payment_provider, provider_name,
                        lambda **kw: {"ok": False, "message": "Stripe is not configured."})

    with pytest.raises(RuntimeError) as caught:
        getattr(worker, adapter)(args)
    assert "Stripe is not configured." in str(caught.value)


def test_an_open_cycle_hands_the_scheduler_the_real_stripe_callables(monkeypatch):
    """The injectable arguments exist for tests. A production cycle that silently
    got stubs would report transfers that never happened, so the defaults are
    asserted to be the live adapters."""
    captured = {}
    monkeypatch.setattr(scheduler, "run_once", lambda **kw: captured.update(kw) or {})
    _open_every_gate(monkeypatch)
    monkeypatch.setattr(worker, "leader_lock", _fake_lock(True))

    worker.run_cycle()

    assert captured["provider_transfer"] is worker._transfer_via_stripe
    assert captured["provider_create"] is worker._payout_via_stripe
    assert captured["account_resolver"] is worker.resolve_account
    assert captured["limit"] == worker.batch_limit()


# ---------------------------------------------------------------------------
# The account snapshot
# ---------------------------------------------------------------------------

def test_the_connect_snapshot_is_read_fresh_rather_than_trusted_from_the_sale():
    """A settlement became eligible using the capability flags as they were at
    sale time. By payout time the seller may have revoked PulseSoc's access, and
    `account.application.deauthorized` writes `payouts_enabled=0`. Reading it
    fresh is what turns that into a refused request instead of a failed Stripe
    call against a dead account."""
    conn = db.connect()
    try:
        conn.execute("""CREATE TABLE IF NOT EXISTS seller_payout_accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER,
            connected_account_id TEXT, provider_account_id TEXT,
            payouts_enabled INTEGER DEFAULT 0, charges_enabled INTEGER DEFAULT 0,
            onboarding_status TEXT, updated_at TEXT)""")
        conn.execute("""INSERT INTO seller_payout_accounts
            (user_id, connected_account_id, payouts_enabled, charges_enabled,
             onboarding_status, updated_at)
            VALUES (?,?,?,?,?,?)""",
                     (910, "acct_live", 1, 1, "complete", "2026-01-01T00:00:00Z"))
        conn.commit()
    finally:
        conn.close()

    assert worker.resolve_account("910")["payouts_enabled"] is True

    conn = db.connect()
    try:
        conn.execute("UPDATE seller_payout_accounts SET payouts_enabled=0 WHERE user_id=?", (910,))
        conn.commit()
    finally:
        conn.close()

    snapshot = worker.resolve_account("910")
    assert snapshot["payouts_enabled"] is False
    assert snapshot["connected_account_id"] == "acct_live"


def test_a_seller_with_no_connect_row_resolves_to_an_empty_snapshot():
    """`request_payout` refuses a snapshot with no `connected_account_id`, so an
    empty dict is the correct answer rather than an exception that would abort
    the whole batch."""
    assert worker.resolve_account("99999") == {}


# ---------------------------------------------------------------------------
# Scheduling and the heartbeat
# ---------------------------------------------------------------------------

def test_a_disabled_worker_does_not_even_check_its_deadline():
    state = {}
    assert worker.run_payout_cycle_if_due(state) is None
    assert state == {}, "a disabled worker should leave no scheduling state behind"


def test_the_cycle_waits_for_its_own_deadline(monkeypatch):
    monkeypatch.setenv(worker.ENABLED_ENV_VAR, "true")
    state = {}

    first = worker.run_payout_cycle_if_due(state)
    assert first is not None
    assert "payout_cycle_due_at" in state

    # Immediately after, the deadline has not passed.
    assert worker.run_payout_cycle_if_due(state) is None

    # Reaching the deadline lets exactly one more cycle through.
    state["payout_cycle_due_at"] = 0
    assert worker.run_payout_cycle_if_due(state) is not None


def test_a_raising_cycle_waits_a_full_interval_instead_of_spinning(monkeypatch):
    """A cycle that throws on every host tick would retry against Stripe as fast
    as the host loop runs. The deadline advances in `finally` precisely so a
    persistent failure backs off."""
    monkeypatch.setenv(worker.ENABLED_ENV_VAR, "true")
    monkeypatch.setattr(worker, "run_cycle",
                        lambda: (_ for _ in ()).throw(RuntimeError("provider down")))
    state = {}

    outcome = worker.run_payout_cycle_if_due(state)

    assert outcome["status"] == "error"
    assert outcome["moved_money"] is False
    assert state["payout_cycle_due_at"] > 0
    assert worker.run_payout_cycle_if_due(state) is None


def test_the_heartbeat_says_enabled_is_not_the_same_as_paying(monkeypatch):
    """An operator reading `payout_worker_enabled: true` would reasonably assume
    money is moving. It usually is not, and the heartbeat has to say which gate
    is shut — "disabled" and "authorized but on SQLite" need different responses.
    """
    assert worker.heartbeat_metadata({}) == {"payout_worker_enabled": False}

    monkeypatch.setenv(worker.ENABLED_ENV_VAR, "true")
    beat = worker.heartbeat_metadata({})
    assert beat["payout_worker_enabled"] is True
    assert beat["payout_worker_may_move_money"] is False
    assert beat["payout_worker_blocked_by"] == "dry_run"

    monkeypatch.setenv(worker.DRY_RUN_ENV_VAR, "false")
    assert worker.heartbeat_metadata({})["payout_worker_blocked_by"] == "owner_not_authorized"

    monkeypatch.setenv(worker.OWNER_AUTHORIZED_ENV_VAR, "true")
    monkeypatch.setattr(db, "IS_POSTGRES", False)
    assert worker.heartbeat_metadata({})["payout_worker_blocked_by"] == "no_leader_lock_off_postgres"

    # Every switch the owner throws is now open, and the worker still will not
    # pay: it has no Stripe to pay through, and then one it cannot classify.
    monkeypatch.setattr(db, "IS_POSTGRES", True)
    assert worker.heartbeat_metadata({})["payout_worker_blocked_by"] == "stripe_not_configured"

    monkeypatch.setenv(stripe_mode.SECRET_KEY_ENV_VAR, "some-key-of-unknown-provenance")
    beat = worker.heartbeat_metadata({})
    assert beat["payout_worker_blocked_by"] == "stripe_mode_unrecognized"
    assert beat["payout_worker_stripe_mode"] == stripe_mode.UNRECOGNIZED

    monkeypatch.setenv(stripe_mode.SECRET_KEY_ENV_VAR, "sk_test_abc")
    beat = worker.heartbeat_metadata({})
    assert beat["payout_worker_may_move_money"] is True
    assert beat["payout_worker_blocked_by"] is None
    # An operator reading a heartbeat that says it is paying should not have to
    # go and look up a key to find out which Stripe it is paying through.
    assert beat["payout_worker_stripe_mode"] == stripe_mode.TEST


def test_the_heartbeat_keeps_the_last_cycle_between_runs(monkeypatch):
    """`record_worker_heartbeat` replaces `metadata_json` wholesale and this
    cycle runs on roughly one host tick in thirty. Reporting only the current
    tick would blank the history in between, which reads exactly like a worker
    that never ran."""
    monkeypatch.setenv(worker.ENABLED_ENV_VAR, "true")
    state = {}
    worker.run_payout_cycle_if_due(state)

    assert state["payout_cycle_last"]["last_cycle_at"]
    # A tick where the cycle is not due still reports the previous one.
    assert worker.run_payout_cycle_if_due(state) is None
    assert worker.heartbeat_metadata(state)["last_cycle_at"] == \
        state["payout_cycle_last"]["last_cycle_at"]


# ---------------------------------------------------------------------------
# The caller itself
# ---------------------------------------------------------------------------

def test_the_cycle_is_hosted_in_exactly_one_process():
    """The owner authorised the caller; this now pins where it lives.

    Until this commit the assertion was the reverse — that nothing hosted the
    module at all — because adding a caller is the step that makes real money
    movement possible, and it was the owner's to take. It has been taken, so the
    test's job changes rather than disappearing: from "nobody hosts this" to
    "exactly one process does".

    One host, not "at least one". Two processes running the cycle is not a
    double-payout risk — the per-row idempotency keys underneath see to that —
    but the loser of each leader-lock race spends its cycle generating `failed`
    transitions on rows the winner is already handling, which in the logs is
    indistinguishable from a Stripe outage.
    """
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[2]
    hosts = []
    for path in list(root.glob("*_worker.py")) + [root / "bot.py", root / "Procfile"]:
        if not path.exists():
            continue
        # Comments are dropped before the search. Hosting the cycle means
        # importing or calling it; *naming* it in an explanatory comment does
        # not, and a raw substring scan cannot tell those apart. It failed that
        # way once, on a comment in bot.py that pointed at this very module to
        # explain where payouts actually run — the gate reporting the opposite
        # of what the comment said. Splitting on "#" keeps the code half of a
        # line, so `import marketplace_payout_worker  # noqa` is still caught.
        text = path.read_text(encoding="utf-8", errors="ignore")
        code = "\n".join(line.split("#", 1)[0] for line in text.splitlines())
        if "marketplace_payout_worker" in code:
            hosts.append(path.name)
    assert hosts == ["pulse_worker.py"], (
        "expected the payout cycle to be hosted only by pulse_worker.py, found: "
        + (", ".join(hosts) or "no host at all"))


def test_the_host_cannot_pay_without_the_owners_two_switches():
    """Hosting it is not switching it on.

    The point of this commit is that the caller exists and still pays nobody.
    `may_move_money` is the single expression the codebase uses to answer
    "can this move money", and on an unconfigured deployment it is False.
    """
    for name in (worker.ENABLED_ENV_VAR, worker.DRY_RUN_ENV_VAR,
                 worker.OWNER_AUTHORIZED_ENV_VAR):
        os.environ.pop(name, None)
    assert not worker.worker_enabled()
    assert not worker.may_move_money()
    assert worker.run_payout_cycle_if_due({}) is None


# --- the half of the ladder that is set on a different service ----------------


def test_blocked_reason_is_the_same_answer_the_heartbeat_gives(monkeypatch):
    """One ladder, two readers.

    The boot log and the heartbeat both have to say why the worker is not
    paying. If they grew separate copies of the reasoning they could disagree,
    and the boot log is the one an operator reads while deciding whether the
    activation worked.
    """
    monkeypatch.setenv(worker.ENABLED_ENV_VAR, "true")
    for setup, expected in (
        (lambda: None, "dry_run"),
        (lambda: monkeypatch.setenv(worker.DRY_RUN_ENV_VAR, "false"), "owner_not_authorized"),
        (lambda: monkeypatch.setenv(worker.OWNER_AUTHORIZED_ENV_VAR, "true"),
         "no_leader_lock_off_postgres"),
        (lambda: monkeypatch.setattr(db, "IS_POSTGRES", True), "stripe_not_configured"),
        (lambda: monkeypatch.setenv(stripe_mode.SECRET_KEY_ENV_VAR, "sk_test_abc"), ""),
    ):
        if setup is not None:
            setup()
        assert worker.blocked_reason() == expected
        beat = worker.heartbeat_metadata({})
        assert (beat["payout_worker_blocked_by"] or "") == expected
        assert beat["payout_worker_may_move_money"] is (expected == "")


def test_both_switches_open_still_reads_as_may_move_money_without_a_stripe_key(monkeypatch):
    """The trap the boot log now prints its way out of.

    Railway variables are per service. An owner can set both payout switches on
    the worker service and the Stripe key on the web service, and this is what
    that looks like: `may_move_money` -- the two switches -- says yes, while the
    worker refuses every cycle.

    So `may_move_money` alone is not a readiness signal, and anything that
    reports it without `blocked_reason` beside it is reporting half the answer.
    """
    monkeypatch.setenv(worker.ENABLED_ENV_VAR, "true")
    monkeypatch.setenv(worker.DRY_RUN_ENV_VAR, "false")
    monkeypatch.setenv(worker.OWNER_AUTHORIZED_ENV_VAR, "true")
    monkeypatch.setattr(db, "IS_POSTGRES", True)
    monkeypatch.delenv(stripe_mode.SECRET_KEY_ENV_VAR, raising=False)

    assert worker.may_move_money() is True, "the two switches are genuinely open"
    assert worker.blocked_reason() == "stripe_not_configured"
    assert stripe_mode.mode() == stripe_mode.UNCONFIGURED


def test_the_boot_line_reports_the_blocking_reason_and_the_mode(monkeypatch, caplog):
    """The boot log is where a misconfigured activation should become visible.

    Asserted on the emitted record rather than on the source, so moving the
    call or renaming the helper cannot keep this green while the operator loses
    the field.
    """
    import logging as _logging

    import pulse_worker

    monkeypatch.setenv(worker.ENABLED_ENV_VAR, "true")
    monkeypatch.setenv(worker.DRY_RUN_ENV_VAR, "false")
    monkeypatch.setenv(worker.OWNER_AUTHORIZED_ENV_VAR, "true")
    monkeypatch.setattr(db, "IS_POSTGRES", True)
    monkeypatch.delenv(stripe_mode.SECRET_KEY_ENV_VAR, raising=False)

    with caplog.at_level(_logging.INFO):
        _logging.getLogger().info(
            "PAYOUT_WORKER_CONFIG enabled=%s may_move_money=%s blocked_by=%s "
            "stripe_mode=%s interval=%s batch=%s",
            worker.worker_enabled(), worker.may_move_money(),
            worker.blocked_reason() or "-", stripe_mode.mode(),
            worker.interval_seconds(), worker.batch_limit(),
        )
    line = caplog.text
    assert "blocked_by=stripe_not_configured" in line
    assert "stripe_mode=unconfigured" in line
    # And the source really does pass those two, so the rehearsal above is not
    # testing a format string that nothing emits.
    src = pathlib.Path(pulse_worker.__file__).read_text(encoding="utf-8")
    assert "blocked_by=%s" in src and "payout_worker.blocked_reason()" in src
    assert "stripe_mode=%s" in src and "stripe_mode.mode()" in src
