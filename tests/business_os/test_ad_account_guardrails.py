"""Account guardrails — the ceiling, the emergency stop, and their directions.

The tests that matter most here are the two about failure direction, because
they encode a decision that looks like a bug from either side if you only see
half of it:

* a spend-read failure must NOT stop delivery (the ledger is still guarding the
  money, and a platform-wide outage is the bigger harm), and
* a halt-read failure MUST stop delivery (a stop that a failed query can lift is
  not a stop).

The rest pin the things an operator would be furious to discover later: that a
halt is not silently rewriting campaign state, that lifting a halt is its own
audited action, and that this module never touches the ledger.

    python tests/business_os/test_ad_account_guardrails.py
"""

import os
import tempfile

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="ad_guardrails_"), "test.db")
os.environ.setdefault("DATABASE_URL", "sqlite:///" + _TMP_DB)

import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

import uuid  # noqa: E402
from datetime import datetime, timedelta, timezone  # noqa: E402

from services import db  # noqa: E402
from services.business_os.advertising import guardrails  # noqa: E402
from services.business_os.advertising.schema import ensure_schema as _ad_schema  # noqa: E402

_ADV = "adv-guardrail-1"


def _assert(cond, detail=""):
    if not cond:
        raise AssertionError(detail)


def _iso(moment):
    return moment.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def setup_module(module=None):
    _ad_schema()
    guardrails.ensure_schema()


def _clear():
    conn = db.connect()
    try:
        conn.execute("DELETE FROM business_os_ad_account_guardrails")
        conn.execute("DELETE FROM business_os_ad_billing_events")
        conn.execute("DELETE FROM business_os_ad_audit")
        conn.commit()
    finally:
        conn.close()


def _charge(advertiser, cents, *, when=None, status="charged"):
    """A canonical billing event — what the ceiling actually measures."""
    conn = db.connect()
    try:
        conn.execute(
            "INSERT INTO business_os_ad_billing_events (billing_event_id, "
            "advertiser_user_id, campaign_id, source_event_type, "
            "source_event_id, billing_model, unit_price_cents, "
            "total_amount_cents, currency, billing_status, idempotency_key, "
            "created_at) VALUES (?, ?, 'camp-1', 'impression', ?, 'cpm', ?, "
            "?, 'usd', ?, ?, ?)",
            (f"be-{uuid.uuid4().hex[:12]}", advertiser,
             f"src-{uuid.uuid4().hex[:8]}", cents, cents, status,
             f"idem-{uuid.uuid4().hex[:12]}",
             _iso(when or datetime.now(timezone.utc))))
        conn.commit()
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# The ceiling
# --------------------------------------------------------------------------- #

def test_an_account_with_no_guardrail_row_delivers():
    """The overwhelming majority of accounts. Absence must mean 'no limit'."""
    _clear()
    result = guardrails.check(_ADV)
    _assert(result["allowed"] is True, result)
    _assert(result["reason"] == guardrails.REASON_OK, result)


def test_a_zero_limit_means_no_limit_not_no_spending():
    """A column defaulting to 0 must not silently stop every account."""
    _clear()
    guardrails.set_daily_ceiling(_ADV, 0)
    _charge(_ADV, 500_00)
    result = guardrails.check(_ADV)
    _assert(result["allowed"] is True,
            f"a 0 ceiling stopped delivery: {result}")


def test_delivery_stops_at_the_daily_ceiling():
    _clear()
    guardrails.set_daily_ceiling(_ADV, 10_00)
    _charge(_ADV, 9_99)
    _assert(guardrails.check(_ADV)["allowed"] is True, "stopped one cent early")
    _charge(_ADV, 1)
    after = guardrails.check(_ADV)
    _assert(after["allowed"] is False, after)
    _assert(after["reason"] == guardrails.REASON_DAILY_CEILING, after)
    _assert(after["remaining_cents"] == 0, after)


def test_yesterdays_spend_does_not_count_against_todays_ceiling():
    """Otherwise the ceiling is a lifetime cap wearing a daily label."""
    _clear()
    guardrails.set_daily_ceiling(_ADV, 10_00)
    _charge(_ADV, 50_00, when=datetime.now(timezone.utc) - timedelta(days=2))
    result = guardrails.check(_ADV)
    _assert(result["allowed"] is True,
            f"spend from two days ago still counts: {result}")
    _assert(result["spent_today_cents"] == 0, result)


def test_a_failed_charge_is_not_counted_against_the_ceiling():
    """It was never charged, so it cannot consume the budget."""
    _clear()
    guardrails.set_daily_ceiling(_ADV, 10_00)
    _charge(_ADV, 50_00, status="failed")
    _assert(guardrails.check(_ADV)["allowed"] is True)


def test_another_advertisers_spend_does_not_count():
    _clear()
    guardrails.set_daily_ceiling(_ADV, 10_00)
    _charge("some-other-advertiser", 500_00)
    _assert(guardrails.check(_ADV)["allowed"] is True)


def test_a_negative_ceiling_is_refused():
    _clear()
    try:
        guardrails.set_daily_ceiling(_ADV, -1)
    except ValueError:
        return
    raise AssertionError("a negative ceiling was accepted")


# --------------------------------------------------------------------------- #
# The emergency stop
# --------------------------------------------------------------------------- #

def test_the_emergency_stop_stops_delivery():
    _clear()
    guardrails.halt_account_delivery(_ADV, actor="admin-1", reason="policy")
    result = guardrails.check(_ADV)
    _assert(result["allowed"] is False, result)
    _assert(result["reason"] == guardrails.REASON_HALTED, result)


def test_a_halt_outranks_a_ceiling_that_has_room_left():
    """The reason shown must be the real one, not the more comfortable one."""
    _clear()
    guardrails.set_daily_ceiling(_ADV, 1000_00)
    guardrails.halt_account_delivery(_ADV, actor="admin-1", reason="policy")
    result = guardrails.check(_ADV)
    _assert(result["reason"] == guardrails.REASON_HALTED,
            f"a halted account was told it hit its budget: {result}")


def test_lifting_a_halt_restores_delivery():
    _clear()
    guardrails.halt_account_delivery(_ADV, actor="admin-1", reason="policy")
    guardrails.lift_account_halt(_ADV, actor="admin-1", reason="resolved")
    _assert(guardrails.check(_ADV)["allowed"] is True)


def test_lifting_a_halt_preserves_the_ceiling():
    """Lifting a stop must not quietly remove the advertiser's own limit."""
    _clear()
    guardrails.set_daily_ceiling(_ADV, 10_00)
    guardrails.halt_account_delivery(_ADV, actor="admin-1")
    guardrails.lift_account_halt(_ADV, actor="admin-1")
    _assert(guardrails.check(_ADV)["daily_limit_cents"] == 10_00,
            "lifting the halt wiped the ceiling")


def test_a_halt_does_not_rewrite_campaign_state():
    """A halt is temporary. Pausing campaigns would destroy the advertiser's own
    active/paused intent and could not be cleanly undone."""
    import inspect
    source = inspect.getsource(guardrails)
    for forbidden in ("business_os_ad_campaigns", "business_os_ad_campaign_operations"):
        _assert(forbidden not in source,
                f"guardrails writes campaign state via {forbidden}")


def test_every_operator_action_is_audited():
    _clear()
    guardrails.set_daily_ceiling(_ADV, 10_00, actor="admin-1", reason="request")
    guardrails.halt_account_delivery(_ADV, actor="admin-2", reason="policy")
    guardrails.lift_account_halt(_ADV, actor="admin-2", reason="resolved")
    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT action FROM business_os_ad_audit "
            "WHERE advertiser_user_id = ?", (_ADV,)).fetchall()
    finally:
        conn.close()
    actions = {r[0] for r in rows}
    _assert(actions == {"account_daily_ceiling_set", "account_delivery_halted",
                        "account_delivery_halt_lifted"},
            f"an operator action went unaudited: {actions}")


# --------------------------------------------------------------------------- #
# Failure direction — the two that matter most
# --------------------------------------------------------------------------- #

class _RaisingConn:
    """A connection whose reads fail, to prove the direction of each guard."""

    def __init__(self, fail_on):
        self.fail_on = fail_on

    def execute(self, sql, params=()):
        if self.fail_on in sql:
            raise RuntimeError("simulated database failure")
        return _RealRow()

    def close(self):
        pass


class _RealRow:
    def fetchone(self):
        # A row with a live ceiling and no halt, so the ceiling path is reached.
        return ("adv", 10_00, "usd", 0, None, None, None)


def test_an_unreadable_halt_state_stops_delivery():
    """Fails CLOSED. A stop a failed query can lift is not a stop."""
    result = guardrails.check(_ADV, conn=_RaisingConn(
        "business_os_ad_account_guardrails"))
    _assert(result["allowed"] is False, result)
    _assert(result["reason"] == guardrails.REASON_HALT_UNREADABLE, result)
    _assert(result["degraded"] is True, result)


def test_an_unreadable_spend_total_does_not_stop_delivery():
    """Fails OPEN, deliberately.

    The per-campaign budget and the overdraft-guarded ledger are both still in
    force, so the money is safe. Failing closed here would turn a transient read
    error into a total advertising outage.
    """
    result = guardrails.check(_ADV, conn=_RaisingConn(
        "business_os_ad_billing_events"))
    _assert(result["allowed"] is True,
            f"a spend read failure blacked out delivery: {result}")
    _assert(result["degraded"] is True,
            "the degradation was not reported to the caller")


# --------------------------------------------------------------------------- #
# Money authority
# --------------------------------------------------------------------------- #

def test_guardrails_never_move_money():
    """It decides whether an account may deliver. It never charges or refunds.

    Checked structurally rather than by grepping for words: the docstring talks
    about the ledger at length precisely to explain why this module does not
    touch it, so a prose grep would fail on the documentation of the property it
    is trying to verify. What matters is that the ledger is never imported and
    that no money table is ever written.
    """
    import ast
    import inspect
    tree = ast.parse(inspect.getsource(guardrails))

    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
    _assert(not any("ledger" in name for name in imported),
            f"guardrails imports the ledger: {sorted(imported)}")

    # Every SQL string literal in the module, checked for writes to money tables.
    money_tables = ("business_os_ad_billing_events",
                    "business_os_ad_spend_accumulator",
                    "business_os_ad_campaign_funding", "ledger_")
    writes = ("INSERT INTO", "UPDATE ", "DELETE FROM")
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
            continue
        sql = node.value.upper()
        if not any(w in sql for w in writes):
            continue
        for table in money_tables:
            _assert(table.upper() not in sql,
                    f"guardrails writes to the money table {table}: {node.value}")


def test_the_ceiling_reads_canonical_billing_not_the_intelligence_log():
    """ads_intel_events measures delivery and explicitly does not decide what is
    billable. A ceiling built on it would bill against the wrong number."""
    import inspect
    source = inspect.getsource(guardrails.account_spend_today)
    _assert("business_os_ad_billing_events" in source, source)
    _assert("ads_intel" not in source,
            "the ceiling is measuring the intelligence layer's event log")


def test_eligibility_composes_the_guardrail_without_merging_it():
    """The gate must report WHY, not just refuse."""
    import inspect
    from services.business_os.advertising import service
    source = inspect.getsource(service.advertiser_eligibility)
    _assert("guardrail" in source, "eligibility does not consult the guardrail")
    _assert('result["guardrail"]' in source,
            "the guardrail result is not surfaced to the caller")


def _main():
    setup_module()
    tests = [(n, o) for n, o in sorted(globals().items())
             if n.startswith("test_") and callable(o)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"  PASS {name}")
        except Exception as exc:  # noqa: BLE001 — standalone runner
            failed += 1
            print(f"  FAIL {name}: {exc}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(_main())
