"""Advertising Stage 2 — canonical CPM/CPC billing + escrow consumption matrix.

Exercises the billing service (``services.business_os.advertising.billing``) and the
versioned pricing policy (``services.business_os.advertising.pricing``) directly on
top of the shared canonical ledger. bot.py is not importable in the hermetic
sandbox; route adapters are checked structurally elsewhere.

Proves the money-integrity boundary of Stage 2 Part 1:

    an ACCEPTED, billing-eligible impression/click -> an immutable billing decision
    -> (when a whole cent is due) a double-entry ledger posting that debits
    ad_campaign_escrow:<cid> and credits platform:advertising_revenue

with its guardrails: CPM charges a fraction of a cent per impression and carries the
sub-cent remainder in milli-cents so only whole cents ever post and value is
preserved across many impressions; CPC charges the whole per-click rate; a
retried/concurrent bill of the same source event is an idempotent no-op
(duplicate=True) and never double-charges; an ineligible (fraud/self-view) source
event yields an ``ineligible`` record and is never charged; an escrow overdraft is
refused by the ledger's overdraft guard and recorded as ``failed`` /
``budget_exhausted`` with NO overdraft; and the price comes only from the versioned
server-side policy (fail closed — none published raises).

    python tests/business_os/test_advertising_billing.py   # no pytest needed
"""

import os
import tempfile
import uuid
import datetime

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="busos_billing_"), "test.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB
os.environ["BUSINESS_OS_ADVERTISING"] = "on"

import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from services import db  # noqa: E402
from services.business_os.advertising import schema as ad_schema  # noqa: E402
from services.business_os.advertising import pricing, billing, spend  # noqa: E402
from services.business_os.advertising.service import AdvertisingError  # noqa: E402
from services.business_os.ledger import ledger  # noqa: E402


ESCROW = "ad_campaign_escrow:"
REVENUE = "platform:advertising_revenue"


def _now():
    return datetime.datetime.utcnow().isoformat()


def setup_module():
    ad_schema.ensure_schema()
    ledger.ensure_schema()
    # Versioned server-side price: CPM $5.00 per 1000 impressions (500 cents). No
    # hardcoded production price lives in code. CPC is deliberately left unpublished
    # here so test_cpc_fails_closed_until_published can prove the fail-closed path,
    # then publish it.
    pricing.publish_policy("cpm", "usd", 500, actor="admin")


def _mk_event(table, cid, eid, eligible=1):
    conn = db.connect()
    try:
        extra = "impression_event_id, destination_type, destination_ref, " \
            if table.endswith("click_events") else ""
        extra_q = "?, ?, ?, " if table.endswith("click_events") else ""
        extra_v = ("imp_" + eid, "url", "https://x") if table.endswith("click_events") else ()
        conn.execute(
            f"INSERT INTO {table} "
            f"(event_id, delivery_id, {extra}campaign_id, ad_set_id, creative_id, "
            "creative_version, placement, subject_ref, advertiser_user_id, event_at, "
            "dedup_key, fraud_status, billing_eligible, billing_processed, created_at) "
            f"VALUES (?, ?, {extra_q}?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)",
            (eid, "dlv_" + eid) + extra_v + (
                cid, "as1", "cr1", 1, "feed", "viewer1", "adv1", _now(),
                "dk_" + eid, "clean", eligible, _now()))
        conn.commit()
    finally:
        conn.close()


def mk_impr(cid, eid, eligible=1):
    _mk_event("business_os_ad_impression_events", cid, eid, eligible)


def mk_click(cid, eid, eligible=1):
    _mk_event("business_os_ad_click_events", cid, eid, eligible)


def fund(cid, cents):
    ledger.post_entry(
        idempotency_key="fund_" + cid + "_" + uuid.uuid4().hex, actor="test",
        amount_cents=cents, currency="usd", entry_type="escrow_fund",
        source="external:test_funding", destination=ESCROW + cid, reason="fund")


# --- pricing: fail closed ---------------------------------------------------
def test_cpc_fails_closed_until_published():
    # CPC price not yet published -> billing must fail closed, not invent a number.
    try:
        pricing.get_active_policy("cpc", "usd")
    except AdvertisingError as exc:
        assert exc.code == "no_pricing_policy", exc.code
    else:
        raise AssertionError("expected no_pricing_policy before CPC is published")
    # Now publish it for the CPC charge tests that follow.
    pricing.publish_policy("cpc", "usd", 25, actor="admin")


def test_active_price_is_highest_version():
    pricing.publish_policy("cpm", "usd", 600, actor="admin")  # new version
    pol = pricing.get_active_policy("cpm", "usd")
    assert pol["unit_price_cents"] == 600, pol
    assert pol["active"] in (1, True), pol
    pricing.publish_policy("cpm", "usd", 500, actor="admin")  # restore for CPM tests


# --- CPM sub-cent accumulation ----------------------------------------------
def test_cpm_subcent_accumulates_then_flushes_whole_cent():
    cid = "camp_cpm"
    fund(cid, 10000)
    mk_impr(cid, "impr1")
    mk_impr(cid, "impr2")
    r1 = billing.bill_event("impression", "impr1")
    r2 = billing.bill_event("impression", "impr2")
    # 500 milli-cents per impression: first is sub-cent (0 posted), second crosses.
    assert r1["billing_status"] == "processed" and r1["total_amount_cents"] == 0, r1
    assert r1["ledger_txn_reference"] is None, r1
    assert r2["billing_status"] == "processed" and r2["total_amount_cents"] == 1, r2
    assert r2["ledger_txn_reference"], r2
    assert ledger.get_balance(ESCROW + cid) == 9999
    assert ledger.get_balance(REVENUE) == 1


def test_bill_is_idempotent_no_double_charge():
    cid = "camp_cpm"  # continues from previous test
    r = billing.bill_event("impression", "impr2")
    assert r.get("duplicate") is True and r["total_amount_cents"] == 1, r
    # escrow/revenue unchanged by the replay
    assert ledger.get_balance(ESCROW + cid) == 9999
    assert ledger.get_balance(REVENUE) == 1


# --- CPC whole-cent ---------------------------------------------------------
def test_cpc_charges_whole_click_rate():
    cid = "camp_cpc"
    fund(cid, 10000)
    mk_click(cid, "clk1")
    r = billing.bill_event("click", "clk1")
    assert r["billing_status"] == "processed" and r["total_amount_cents"] == 25, r
    assert r["billing_model"] == "cpc", r
    assert ledger.get_balance(ESCROW + cid) == 9975


# --- ineligibility ----------------------------------------------------------
def test_ineligible_source_never_charged():
    cid = "camp_inel"
    fund(cid, 10000)
    mk_click(cid, "clk_bad", eligible=0)
    r = billing.bill_event("click", "clk_bad")
    assert r["billing_status"] == "ineligible" and r["total_amount_cents"] == 0, r
    assert r["eligibility_decision"] == "not_billing_eligible", r
    assert ledger.get_balance(ESCROW + cid) == 10000  # untouched


# --- budget exhaustion (overdraft refused) ----------------------------------
def test_budget_exhaustion_refuses_overdraft():
    cid = "camp_broke"
    fund(cid, 10)  # only 10 cents; a 25c click cannot post
    mk_click(cid, "clk_big")
    r = billing.bill_event("click", "clk_big")
    assert r["billing_status"] == "failed" and r["total_amount_cents"] == 0, r
    assert r["eligibility_decision"] == "budget_exhausted", r
    assert ledger.get_balance(ESCROW + cid) == 10  # NO overdraft
    assert billing.budget_exhausted(cid) is True


# --- source event must exist ------------------------------------------------
def test_missing_source_event_raises():
    try:
        billing.bill_event("impression", "does_not_exist")
    except AdvertisingError as exc:
        assert exc.http_status == 404, exc.http_status
        return
    raise AssertionError("expected 404 for missing source event")


# --- batch driver -----------------------------------------------------------
def test_process_pending_batches_and_totals():
    cid = "camp_batch"
    fund(cid, 10000)
    for i in range(4):
        mk_impr(cid, f"b_impr{i}")
    summ = billing.process_pending(campaign_id=cid)
    assert summ["events"] == 4, summ
    # 4 impressions * 500 milli-cents = 2000 milli-cents = 2 whole cents.
    assert summ["charged_cents"] == 2, summ
    assert ledger.get_balance(ESCROW + cid) == 9998


def test_process_pending_reruns_are_noops():
    cid = "camp_batch"
    summ = billing.process_pending(campaign_id=cid)
    # everything already processed -> nothing new billed
    assert summ["charged_cents"] == 0, summ
    assert ledger.get_balance(ESCROW + cid) == 9998


# --- spend projection + reconciliation --------------------------------------
def _set_budget(cid, cents):
    conn = db.connect()
    try:
        conn.execute(
            "INSERT INTO business_os_ad_campaign_funding "
            "(campaign_id, advertiser_user_id, budget_cents, currency, "
            "reserved_amount_cents, funding_status, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, 'funded', ?, ?)",
            (cid, "adv1", cents, "usd", cents, _now(), _now()))
        conn.commit()
    finally:
        conn.close()


def test_spend_view_is_authoritative():
    cid = "camp_spend"
    fund(cid, 10000)
    _set_budget(cid, 10000)
    for i in range(5):
        mk_impr(cid, f"s_impr{i}")
    billing.process_pending(campaign_id=cid)
    # 5 impressions * 500 milli-cents = 2500 -> 2 whole cents, 500 milli carried.
    sv = spend.get_campaign_spend(cid)
    assert sv["spent_cents"] == 2, sv
    assert sv["accrued_millicents"] == 500, sv
    assert sv["impressions_billed"] == 5, sv
    assert sv["budget_cents"] == 10000, sv
    assert sv["pct_budget_spent"] == 0.02, sv
    assert sv["remaining_escrow_cents"] == 9998, sv
    assert sv["budget_exhausted"] is False, sv
    assert sv["billing_event_counts"]["processed"] == 5, sv
    assert sv["confidence"]["spent_cents"] == "Confirmed", sv


def test_reconcile_consistent_when_sources_agree():
    cid = "camp_spend"  # continues from previous
    rec = spend.reconcile_campaign(cid)
    assert rec["consistent"] is True, rec
    assert rec["billing_events_cents"] == 2, rec
    assert rec["ledger_charged_cents"] == 2, rec
    assert rec["accumulator_billed_cents"] == 2, rec
    assert rec["accrued_millicents"] == 500, rec


def test_projection_is_modeled_and_never_fabricated():
    proj = spend.project_budget_exhaustion("camp_spend")
    assert proj["confidence"] == "Modeled", proj
    assert proj["remaining_escrow_cents"] == 9998, proj
    # basis is an honest label, never a made-up date with no history
    assert proj["basis"] in (
        "insufficient_history", "single_point", "linear_extrapolation"), proj


def test_reconcile_reports_drift_without_repair():
    cid = "camp_spend"
    # Inject a rogue ledger charge that has no matching billing event.
    ledger.post_entry(
        idempotency_key="rogue_" + uuid.uuid4().hex, actor="test",
        amount_cents=7, currency="usd", entry_type=spend.LEDGER_CHARGE_ENTRY_TYPE,
        source=ESCROW + cid, destination=REVENUE, reason="rogue",
        related_object="ad_campaign:" + cid)
    rec = spend.reconcile_campaign(cid)
    assert rec["consistent"] is False, rec
    assert len(rec["discrepancies"]) >= 1, rec
    # No silent repair: ledger moved to 9, billing events still record 2.
    assert rec["ledger_charged_cents"] == 9, rec
    assert rec["billing_events_cents"] == 2, rec


# --- standalone runner ------------------------------------------------------
def _run_standalone():
    setup_module()
    tests = [
        test_cpc_fails_closed_until_published,
        test_active_price_is_highest_version,
        test_cpm_subcent_accumulates_then_flushes_whole_cent,
        test_bill_is_idempotent_no_double_charge,
        test_cpc_charges_whole_click_rate,
        test_ineligible_source_never_charged,
        test_budget_exhaustion_refuses_overdraft,
        test_missing_source_event_raises,
        test_process_pending_batches_and_totals,
        test_process_pending_reruns_are_noops,
        test_spend_view_is_authoritative,
        test_reconcile_consistent_when_sources_agree,
        test_projection_is_modeled_and_never_fabricated,
        test_reconcile_reports_drift_without_repair,
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
