"""The sweep that checks whether the money chain did what its metrics claim.

Every other suite in this directory proves a step of the chain works. This one is
about the step after the last one: noticing when a link that worked yesterday has
quietly stopped, with a seller's money parked on the platform's side of it.

Two halves, and the split is the point. The engine
(``business_os.payments.reconciliation.reconcile_marketplace_settlements``)
decides what counts as stuck; ``payments_reconciliation_cycle`` decides only when
to ask and stays out of the answer. So the engine tests build real settlement
rows and assert on findings, and the cycle tests never touch a settlement at all
— they assert that hosting the engine did not turn it on, that its interval
cannot degenerate, and that a critical someone already saw still reads as
critical the next time round.
"""
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone

import pytest

os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(
    tempfile.mkdtemp(prefix="marketplace_reconcile_"), "test.db")
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from services import db  # noqa: E402
from services import marketplace_commercial_operations as commercial  # noqa: E402
from services import marketplace_settlement_service as settlement  # noqa: E402
from services import payments_reconciliation_cycle as cycle  # noqa: E402
from services.business_os.ledger import ledger  # noqa: E402
from services.business_os.payments import incidents, reconciliation  # noqa: E402

ALL_FLAGS = (cycle.RECONCILIATION_ENABLED_ENV_VAR, cycle.INTERVAL_ENV_VAR)


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for name in ALL_FLAGS:
        monkeypatch.delenv(name, raising=False)
    ledger.ensure_schema()
    settlement.ensure_schema()
    incidents.ensure_schema()
    reconciliation.ensure_schema()
    conn = db.connect()
    try:
        # Each test owns the whole table. These checks are about what is *in* a
        # state, so a row left behind by the previous test is not noise — it is
        # a finding, attributed to the wrong test.
        conn.execute("DELETE FROM marketplace_commercial_settlements")
        conn.execute("DELETE FROM financial_incidents")
        conn.commit()
    finally:
        conn.close()


def _tx(tx_id):
    quote = {"quote_id": f"q{tx_id}", "fee_policy_version": "MARKETPLACE_LEGACY_CURRENT",
             "payout_policy_version": "MARKETPLACE_PAYOUTS_V1", "platform_fee_bps": 1000,
             "merchandise_net_minor": 10000, "shipping_minor": 0, "tax_minor": 0,
             "seller_shipping_credit_minor": 0, "buyer_total_minor": 10000}
    return {"id": tx_id, "seller_user_id": 22, "item_type": "marketplace_product",
            "amount_cents": 10000, "platform_fee_cents": 1000, "seller_net_cents": 9000,
            "currency": "USD", "metadata_json": json.dumps({"commercial_quote": quote})}


def _hours_ago(hours):
    return (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime(
        "%Y-%m-%dT%H:%M:%S.%fZ")


def _settlement(tx_id, **columns):
    """A real settlement row, then forced into the state under test.

    Written through ``settle_paid_transaction`` rather than INSERTed, so the row
    carries whatever the real code puts in the columns this check does not name.
    A hand-built row would let the check pass against a shape production never
    produces.
    """
    settlement.settle_paid_transaction(_tx(tx_id), payout_ready=True)
    if not columns:
        return
    conn = db.connect()
    try:
        assignments = ", ".join(f"{name}=?" for name in columns)
        conn.execute(
            f"UPDATE marketplace_commercial_settlements SET {assignments} "
            "WHERE seller_transaction_id=?",
            (*columns.values(), tx_id))
        conn.commit()
    finally:
        conn.close()


def _codes(result_key=None):
    """The finding codes on every incident the last check opened."""
    found = []
    for row in incidents.list_incidents(domain="seller_payments", limit=200)["incidents"]:
        details = row.get("details") or {}
        found.append(details.get("code"))
    return found


# --- the engine: rows nothing will ever move --------------------------------

def test_a_deployment_with_no_settlements_finds_nothing_and_says_so():
    out = reconciliation.reconcile_marketplace_settlements()
    assert out["tables_missing"] is False
    assert out["settlements_total"] == 0
    assert out["incidents"] == []


def test_a_healthy_chain_opens_no_incident():
    _settlement(101, payout_state="protection_hold",
                protection_ends_at=_hours_ago(-48))  # hold ends in the future
    _settlement(102, payout_state="paid", provider_payout_id="po_live_ok")
    out = reconciliation.reconcile_marketplace_settlements()
    assert out["incidents"] == []
    assert out["snapshot_mismatches"] == 0


def test_scheduled_without_a_provider_id_is_critical(monkeypatch):
    """The finding this check exists for.

    ``apply_provider_event`` matches settlements *by* ``provider_payout_id``, so
    a row that reached ``scheduled`` without one cannot be reached by any Stripe
    webhook. It is not slow; there is nothing left in the system that would ever
    move it, and the seller's money stays fenced until a human notices.
    """
    _settlement(201, payout_state="scheduled", provider_payout_id=None,
                updated_at=_hours_ago(24))
    out = reconciliation.reconcile_marketplace_settlements()
    assert out["scheduled_without_provider_id"] == 1
    assert "payout_scheduled_without_provider_id" in _codes()

    opened = incidents.list_incidents(domain="seller_payments", limit=10)["incidents"]
    assert opened[0]["severity"] == "critical"


def test_a_scheduled_row_gets_the_two_network_calls_before_it_is_called_stuck():
    """The gap between the `scheduled` transition and the id being written is two
    Stripe calls wide, not hours. A row inside that window is mid-flight."""
    _settlement(202, payout_state="scheduled", provider_payout_id=None,
                updated_at=_hours_ago(1))
    out = reconciliation.reconcile_marketplace_settlements()
    assert out["scheduled_without_provider_id"] == 0


def test_a_hold_with_no_end_can_never_elapse_so_it_is_critical():
    _settlement(301, payout_state="protection_hold", protection_ends_at=None)
    out = reconciliation.reconcile_marketplace_settlements()
    assert out["protection_hold_without_end"] == 1
    assert "protection_hold_without_end" in _codes()


def test_a_hold_long_past_its_end_says_the_sweep_is_not_running():
    _settlement(401, payout_state="protection_hold",
                protection_ends_at=_hours_ago(48))
    out = reconciliation.reconcile_marketplace_settlements()
    assert out["protection_hold_not_released"] == 1
    assert "protection_hold_not_released" in _codes()


def test_the_hold_grace_is_measured_from_the_hold_end_not_the_last_write():
    """An unrelated UPDATE must not restart the clock.

    ``updated_at`` moves on any write — a refund, an admin note, a schema
    backfill — so measuring from it would hide exactly the rows whose hold
    expired long ago and which something keeps touching.
    """
    _settlement(402, payout_state="protection_hold",
                protection_ends_at=_hours_ago(48), updated_at=_hours_ago(0))
    assert reconciliation.reconcile_marketplace_settlements()[
        "protection_hold_not_released"] == 1


def test_eligible_and_untouched_says_the_payout_scheduler_is_not_running():
    _settlement(501, payout_state="eligible", payout_ready=1, blocker_code=None,
                updated_at=_hours_ago(48))
    out = reconciliation.reconcile_marketplace_settlements()
    assert out["eligible_but_never_scheduled"] == 1
    assert "eligible_but_never_scheduled" in _codes()


def test_a_blocked_eligible_row_is_not_the_schedulers_fault():
    """The scheduler's own predicate skips a blocked row, so reporting one would
    accuse a component that behaved correctly."""
    _settlement(502, payout_state="eligible", payout_ready=1,
                blocker_code="SELLER_NOT_PAYOUT_READY", updated_at=_hours_ago(48))
    _settlement(503, payout_state="eligible", payout_ready=0,
                updated_at=_hours_ago(48))
    assert reconciliation.reconcile_marketplace_settlements()[
        "eligible_but_never_scheduled"] == 0


def test_a_state_the_machine_does_not_define_is_reported():
    _settlement(601, payout_state="somebody_invented_this")
    out = reconciliation.reconcile_marketplace_settlements()
    assert out["payout_state_unknown"] == 1
    assert "payout_state_not_in_state_machine" in _codes()


def test_every_state_the_machine_does_define_is_accepted():
    """A guard against the inverse failure: a check that calls the real states
    unknown would bury a real finding under one alarm per order."""
    for offset, state in enumerate(sorted(settlement.PAYOUT_STATES)):
        _settlement(700 + offset, payout_state=state)
    assert reconciliation.reconcile_marketplace_settlements()[
        "payout_state_unknown"] == 0


# --- the arithmetic invariant, asked in one place ---------------------------

def test_a_settlement_that_does_not_add_up_is_a_balance_mismatch():
    _settlement(801, net_seller_earnings_minor=9999)
    out = reconciliation.reconcile_marketplace_settlements()
    assert out["snapshot_mismatches"] == 1
    assert "commercial_snapshot_mismatch" in _codes()


def test_the_drift_is_reported_as_a_number_not_a_boolean():
    _settlement(802, net_platform_fee_minor=1042)
    reconciliation.reconcile_marketplace_settlements()
    opened = incidents.list_incidents(domain="seller_payments", limit=10)["incidents"]
    assert opened[0]["details"]["drift_minor"]["platform_fee_minor"] == 42


def test_a_cent_of_drift_is_not_as_loud_as_a_dollar():
    _settlement(803, net_platform_fee_minor=1001)
    reconciliation.reconcile_marketplace_settlements()
    assert incidents.list_incidents(
        domain="seller_payments", limit=10)["incidents"][0]["severity"] == "warning"


def test_both_reconcilers_ask_the_same_question():
    """The invariant has one implementation, so the two callers cannot disagree.

    Before this, ``marketplace_commercial_operations.reconcile`` spelled the
    expression out inline and this check would have needed a second copy. A
    second copy of a drift detector is free to drift.
    """
    _settlement(804, net_seller_earnings_minor=8000)
    engine = reconciliation.reconcile_marketplace_settlements()
    legacy = commercial.reconcile()
    assert engine["snapshot_mismatches"] == len(legacy["findings"]) == 1
    assert legacy["findings"][0]["seller_transaction_id"] == 804


def test_a_row_too_malformed_for_arithmetic_is_still_reported(monkeypatch):
    """Reported, not skipped, and not fatal to the sweep.

    Forced through ``snapshot_drift`` rather than by writing a bad row, because
    the schema's NOT NULL constraints make that shape unreachable through the
    columns this check reads. The branch still earns its keep: the way a row
    arrives unreadable here is a column that a migration has not added yet, and
    at that moment every row in the table takes this path. Raising would mean
    one absent column blinds the sweep entirely.
    """
    monkeypatch.setattr(commercial, "snapshot_drift",
                        lambda row: (_ for _ in ()).throw(KeyError("net_fee_minor")))
    _settlement(805)
    out = reconciliation.reconcile_marketplace_settlements()
    assert out["snapshot_mismatches"] == 1
    assert "commercial_snapshot_mismatch" in _codes()


def test_the_schema_will_not_hold_a_settlement_with_no_earnings():
    """Why the branch above has to be provoked. The first line of defence is the
    column definition, and it is worth knowing it is still there."""
    _settlement(806)
    conn = db.connect()
    try:
        with pytest.raises(Exception):
            conn.execute("UPDATE marketplace_commercial_settlements "
                         "SET net_seller_earnings_minor=NULL "
                         "WHERE seller_transaction_id=?", (806,))
            conn.commit()
    finally:
        conn.close()


# --- a partial answer must not read as a clean one --------------------------

def test_a_truncated_scan_says_so(monkeypatch):
    """The one way this report can be clean and wrong at the same time.

    The arithmetic check is the only one with no selective predicate, so it is
    the only one bounded by a row cap. A table past the cap is half-checked, and
    an unexamined row must not be indistinguishable from a balanced one.
    """
    monkeypatch.setattr(reconciliation, "SNAPSHOT_SCAN_LIMIT", 2)
    for tx_id in (901, 902, 903):
        _settlement(tx_id)
    out = reconciliation.reconcile_marketplace_settlements()
    assert out["settlements_total"] == 3
    assert out["settlements_scanned"] == 2
    assert out["scan_truncated"] is True


def test_an_untruncated_scan_says_that_too():
    _settlement(904)
    assert reconciliation.reconcile_marketplace_settlements()["scan_truncated"] is False


# --- incidents are refreshed, not duplicated --------------------------------

def test_running_twice_does_not_open_the_same_finding_twice():
    """A sweep on an hourly schedule meets a week-old stall 168 times. Each one
    must land on the same row, or the incident list becomes unreadable exactly
    when someone needs to read it."""
    _settlement(1001, payout_state="scheduled", provider_payout_id=None,
                updated_at=_hours_ago(24))
    reconciliation.reconcile_marketplace_settlements()
    reconciliation.reconcile_marketplace_settlements()
    opened = incidents.list_incidents(domain="seller_payments", limit=50)["incidents"]
    assert len(opened) == 1


def test_a_systemic_stall_is_counted_in_full_even_though_it_is_not_listed_in_full(
        monkeypatch):
    """The cap bounds the noise, not the number. If every order stalls, the
    count is the finding — capping it would understate the outage."""
    monkeypatch.setattr(reconciliation, "MARKETPLACE_INCIDENT_CAP", 2)
    for tx_id in (1101, 1102, 1103, 1104):
        _settlement(tx_id, payout_state="eligible", payout_ready=1,
                    blocker_code=None, updated_at=_hours_ago(48))
    out = reconciliation.reconcile_marketplace_settlements()
    assert out["eligible_but_never_scheduled"] == 4
    assert len(incidents.list_incidents(
        domain="seller_payments", limit=50)["incidents"]) == 2
    assert incidents.list_incidents(
        domain="seller_payments", limit=1)["incidents"][0][
            "details"]["occurrences_this_run"] == 4


def test_the_sweep_transitions_nothing():
    """Detect and report. A reconciler that repairs is a reconciler nobody can
    trust to tell them what actually happened."""
    _settlement(1201, payout_state="eligible", payout_ready=1, blocker_code=None,
                updated_at=_hours_ago(48))
    before = settlement.get_settlement(1201)
    reconciliation.reconcile_marketplace_settlements()
    assert settlement.get_settlement(1201) == before


# --- standing state vs. this run's output -----------------------------------

def test_an_old_critical_still_counts_when_the_new_sweep_finds_nothing():
    _settlement(1301, payout_state="scheduled", provider_payout_id=None,
                updated_at=_hours_ago(24))
    reconciliation.reconcile_marketplace_settlements()
    assert reconciliation.open_critical_incidents() == 1

    conn = db.connect()
    try:
        conn.execute("DELETE FROM marketplace_commercial_settlements")
        conn.commit()
    finally:
        conn.close()
    out = reconciliation.reconcile_marketplace_settlements()
    assert out["incidents"] == []
    # Nothing was found and nothing was fixed. Those are different facts.
    assert reconciliation.open_critical_incidents() == 1


def test_acknowledged_is_not_resolved():
    """Someone has seen it; the money has not moved. A count that dropped here
    would let an incident be silenced by being read."""
    _settlement(1401, payout_state="scheduled", provider_payout_id=None,
                updated_at=_hours_ago(24))
    reconciliation.reconcile_marketplace_settlements()
    opened = incidents.list_incidents(domain="seller_payments", limit=1)["incidents"][0]
    incidents.update_incident_status(opened["id"], "acknowledged", actor="ops")
    assert reconciliation.open_critical_incidents() == 1

    incidents.update_incident_status(opened["id"], "resolved",
                                     "transfer re-issued by hand", "ops")
    assert reconciliation.open_critical_incidents() == 0


def test_a_warning_is_not_a_critical():
    _settlement(1501, payout_state="protection_hold",
                protection_ends_at=_hours_ago(48))
    reconciliation.reconcile_marketplace_settlements()
    assert reconciliation.open_critical_incidents() == 0


# --- the cycle: hosting it did not switch it on -----------------------------

def test_an_unconfigured_deployment_reconciles_nothing():
    assert not cycle.reconciliation_enabled()
    assert cycle.run_reconciliation_cycle_if_due({}) is None


@pytest.mark.parametrize("value", ["", "   ", "maybe", "2", "enabled"])
def test_an_unclear_flag_leaves_the_sweep_off(monkeypatch, value):
    monkeypatch.setenv(cycle.RECONCILIATION_ENABLED_ENV_VAR, value)
    assert not cycle.reconciliation_enabled()


@pytest.mark.parametrize("value,expected", [
    ("", 3600), ("abc", 3600), ("1", 300), ("999999", 86400), ("900", 900)])
def test_the_interval_cannot_degenerate(monkeypatch, value, expected):
    """An unparseable value falls back to the default, not to the floor: a typo
    should not make the most expensive read in the worker run twelve times as
    often as intended."""
    monkeypatch.setenv(cycle.INTERVAL_ENV_VAR, value)
    assert cycle.interval_seconds() == expected


def test_the_flag_is_read_per_call_not_at_import(monkeypatch):
    assert not cycle.reconciliation_enabled()
    monkeypatch.setenv(cycle.RECONCILIATION_ENABLED_ENV_VAR, "true")
    assert cycle.reconciliation_enabled()


def test_a_cycle_that_just_ran_does_not_run_again(monkeypatch):
    runs = []
    monkeypatch.setenv(cycle.RECONCILIATION_ENABLED_ENV_VAR, "true")
    monkeypatch.setattr(cycle, "run_cycle", lambda: runs.append(1) or {})
    state: dict = {}
    cycle.run_reconciliation_cycle_if_due(state)
    cycle.run_reconciliation_cycle_if_due(state)
    assert len(runs) == 1


def test_a_sweep_that_raises_waits_a_full_interval_rather_than_spinning(monkeypatch):
    """The deadline advances in ``finally``. For a sweep that scans several
    tables, retrying on every host tick is the difference between a failure and
    an outage."""
    def _boom():
        raise RuntimeError("database gone")

    monkeypatch.setenv(cycle.RECONCILIATION_ENABLED_ENV_VAR, "true")
    monkeypatch.setattr(cycle, "run_cycle", _boom)
    state: dict = {}
    with pytest.raises(RuntimeError):
        cycle.run_reconciliation_cycle_if_due(state)
    assert state["reconciliation_due_at"] is not None
    assert cycle.run_reconciliation_cycle_if_due(state) is None


def test_a_failed_engine_is_reported_rather_than_raised(monkeypatch):
    """``run_all`` contains its own per-check failures, so one reaching here is
    the sweep itself failing — worth naming as its own thing."""
    monkeypatch.setattr(reconciliation, "run_all",
                        lambda: (_ for _ in ()).throw(RuntimeError("no db")))
    assert "no db" in cycle.run_cycle()["error"]


# --- what an operator reads between sweeps ----------------------------------

def test_the_heartbeat_says_off_when_it_is_off():
    assert cycle.heartbeat_metadata({}) == {"reconciliation_enabled": False}


def test_the_metrics_survive_the_ticks_between_sweeps(monkeypatch):
    """``record_worker_heartbeat`` replaces metadata wholesale and this sweep
    runs on one host tick in a few hundred, so reporting only the current tick
    would read identically to a sweep that never ran."""
    monkeypatch.setenv(cycle.RECONCILIATION_ENABLED_ENV_VAR, "true")
    state: dict = {}
    cycle.run_reconciliation_cycle_if_due(state)
    first = cycle.heartbeat_metadata(state)
    assert first["last_reconciliation_at"]

    # A tick where the deadline has not passed must not blank what it says.
    assert cycle.run_reconciliation_cycle_if_due(state) is None
    assert cycle.heartbeat_metadata(state)["last_reconciliation_at"] == \
        first["last_reconciliation_at"]


def test_the_heartbeat_carries_the_standing_critical_count(monkeypatch):
    monkeypatch.setenv(cycle.RECONCILIATION_ENABLED_ENV_VAR, "true")
    _settlement(1601, payout_state="scheduled", provider_payout_id=None,
                updated_at=_hours_ago(24))
    state: dict = {}
    cycle.run_reconciliation_cycle_if_due(state)
    beat = cycle.heartbeat_metadata(state)
    assert beat["last_reconciliation_critical_open"] == 1
    assert beat["last_marketplace_stuck_scheduled"] == 1


def test_a_critical_finding_is_logged_loudly(monkeypatch, caplog):
    """The one line in the cycle that raises its voice. At INFO it would share a
    stream with the hundreds of sweeps that found nothing."""
    monkeypatch.setenv(cycle.RECONCILIATION_ENABLED_ENV_VAR, "true")
    _settlement(1701, payout_state="scheduled", provider_payout_id=None,
                updated_at=_hours_ago(24))
    with caplog.at_level("ERROR"):
        cycle.run_reconciliation_cycle_if_due({})
    assert "PAYMENTS_RECONCILIATION_CRITICAL" in caplog.text


def test_a_clean_sweep_is_not_logged_as_an_error(monkeypatch, caplog):
    monkeypatch.setenv(cycle.RECONCILIATION_ENABLED_ENV_VAR, "true")
    with caplog.at_level("ERROR"):
        cycle.run_reconciliation_cycle_if_due({})
    assert "PAYMENTS_RECONCILIATION_CRITICAL" not in caplog.text


def test_the_truncation_flag_reaches_the_heartbeat(monkeypatch):
    monkeypatch.setenv(cycle.RECONCILIATION_ENABLED_ENV_VAR, "true")
    monkeypatch.setattr(reconciliation, "SNAPSHOT_SCAN_LIMIT", 1)
    _settlement(1801)
    _settlement(1802)
    state: dict = {}
    cycle.run_reconciliation_cycle_if_due(state)
    assert cycle.heartbeat_metadata(state)["last_marketplace_scan_truncated"] is True


# --- the engine is wired into the full sweep --------------------------------

def test_the_marketplace_check_runs_as_part_of_run_all():
    """A check nobody calls is not a check. This is the only assertion that
    proves the new one is reachable from the thing the cycle actually runs."""
    _settlement(1901, payout_state="scheduled", provider_payout_id=None,
                updated_at=_hours_ago(24))
    summary = reconciliation.run_all()
    assert summary["checks"]["marketplace_settlements"][
        "scheduled_without_provider_id"] == 1
    assert summary["open_critical_incidents"] >= 1


def test_a_broken_marketplace_check_does_not_blind_the_others(monkeypatch):
    monkeypatch.setattr(reconciliation, "reconcile_marketplace_settlements",
                        lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    summary = reconciliation.run_all()
    assert summary["check_errors"] == 1
    assert "boom" in summary["checks"]["marketplace_settlements"]["error"]
    assert "ledger_balances" in summary["checks"]
