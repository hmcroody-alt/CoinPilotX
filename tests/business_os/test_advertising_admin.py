"""Advertising Stage 2 — consolidated admin governance surface (Part 6).

Proves the Part-6 admin gaps are wired and GOVERNED (spec: "sensitive actions require
role + explicit reason + audit + before/after"):

  * billing inspection returns per-campaign money totals reconciled against escrow;
  * fraud summary aggregates clean vs flagged events;
  * spend halt/lift, advertiser restrict/lift-restriction, and appeal resolution each
    REQUIRE a non-empty actor and a non-empty reason (else 400), write an append-only
    audit row, and return an explicit before/after;
  * an advertiser can appeal and an admin can grant it, which lifts the restriction in
    the same governed action; a resolved appeal cannot be resolved twice.

    python tests/business_os/test_advertising_admin.py   # no pytest needed
"""

import os
import tempfile
import uuid
import datetime

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="busos_adadmin_"), "test.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB
os.environ["BUSINESS_OS_ADVERTISING"] = "on"

import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from services import db  # noqa: E402
from services.business_os import schema_bootstrap  # noqa: E402
from services.business_os.advertising import service as ad  # noqa: E402
from services.business_os.advertising import pricing, billing  # noqa: E402
from services.business_os.advertising import admin  # noqa: E402
from services.business_os.advertising.service import AdvertisingError  # noqa: E402
from services.business_os.ledger import ledger  # noqa: E402


OWNER = 800
ADMIN = 9
ESCROW = "ad_campaign_escrow:"


def setup_module(module=None):
    # The canonical bootstrap production runs, rather than a hand-picked pair of
    # ensure_schema calls. Eligibility consults advertising.guardrails, whose table
    # lives behind its own ensure_schema; naming only the advertising + ledger ones
    # left the guardrail read failing, and it deliberately fails CLOSED, so every
    # advertiser here was refused with account_halt_state_unreadable. That only
    # stayed hidden because test_ad_account_guardrails.py sorts earlier and created
    # the table for the whole session.
    schema_bootstrap.ensure_all()
    pricing.publish_policy("cpm", "usd", 500, actor="admin")
    pricing.publish_policy("cpc", "usd", 25, actor="admin")
    conn = db.connect()
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS users ("
            "user_id INTEGER PRIMARY KEY, account_status TEXT DEFAULT 'active', "
            "access_enabled INTEGER DEFAULT 1)")
        conn.commit()
    finally:
        conn.close()


def _ctx():
    return {"account_status": "active", "access_enabled": 1}


def _approve(uid):
    ad.upsert_advertiser(uid)
    ad.set_advertiser_status(uid, "approved", actor=ADMIN)


def _now():
    return datetime.datetime.utcnow().isoformat()


def _fund(cid, cents):
    ledger.post_entry(
        idempotency_key="fund_" + cid + "_" + uuid.uuid4().hex, actor="test",
        amount_cents=cents, currency="usd", entry_type="escrow_fund",
        source="external:test_funding", destination=ESCROW + cid, reason="fund")


def _real_campaign(uid=OWNER, name="c"):
    c = ad.create_campaign_draft(uid, name=name, objective="traffic", context=_ctx())
    return c["campaign_id"]


def _mk_click(cid, eid, fraud="clean", eligible=1):
    conn = db.connect()
    try:
        conn.execute(
            "INSERT INTO business_os_ad_click_events "
            "(event_id, delivery_id, impression_event_id, destination_type, "
            "destination_ref, campaign_id, ad_set_id, creative_id, creative_version, "
            "placement, subject_ref, advertiser_user_id, event_at, dedup_key, "
            "fraud_status, billing_eligible, billing_processed, created_at) "
            "VALUES (?, ?, ?, 'url', 'https://x', ?, 'as1', 'cr1', 1, 'feed', "
            "'viewer1', ?, ?, ?, ?, ?, 0, ?)",
            (eid, "dlv_" + eid, "imp_" + eid, cid, OWNER, _now(),
             "dk_" + eid, fraud, eligible, _now()))
        conn.commit()
    finally:
        conn.close()


def _force_active(cid):
    """Put a campaign's operational row directly into 'active' (bypasses the funding
    flow, which is exercised by the funding/operations suites, not this admin one)."""
    conn = db.connect()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO business_os_ad_campaign_operations "
            "(campaign_id, advertiser_user_id, operational_status, activated_at, "
            "created_at, updated_at) VALUES (?, ?, 'active', ?, ?, ?)",
            (cid, ad._sid(OWNER), _now(), _now(), _now()))
        conn.commit()
    finally:
        conn.close()


def _expect_error(fn, code=None, http=None):
    try:
        fn()
    except AdvertisingError as e:
        if code is not None:
            assert e.code == code, f"expected code {code}, got {e.code}"
        if http is not None:
            assert e.http_status == http, f"expected http {http}, got {e.http_status}"
        return
    raise AssertionError(f"expected AdvertisingError(code={code}, http={http})")


def _audit_count(action, uid=None):
    conn = db.connect()
    try:
        if uid is None:
            row = conn.execute(
                "SELECT COUNT(*) FROM business_os_ad_audit WHERE action = ?",
                (action,)).fetchone()
        else:
            row = conn.execute(
                "SELECT COUNT(*) FROM business_os_ad_audit "
                "WHERE action = ? AND advertiser_user_id = ?",
                (action, ad._sid(uid))).fetchone()
        return int(row[0])
    finally:
        conn.close()


# --- billing inspection -----------------------------------------------------
def test_billing_summary_reconciles_spend_and_escrow():
    _approve(OWNER)
    cid = _real_campaign(name="admin-bill")
    _fund(cid, 1000)
    _mk_click(cid, "clk_ok")
    r = billing.bill_event("click", "clk_ok")
    assert r["billing_status"] == "processed", r
    summ = admin.admin_billing_summary(cid)
    assert summ["spent_cents"] == 25, summ           # one 25c CPC click
    assert summ["escrow_balance_cents"] == 975, summ  # 1000 - 25
    assert summ["billed_events"]["processed"]["count"] == 1, summ
    events = admin.admin_list_billing_events(campaign_id=cid)
    assert len(events) == 1 and events[0]["billing_status"] == "processed", events


# --- fraud signals ----------------------------------------------------------
def test_fraud_summary_counts_clean_vs_flagged():
    _approve(OWNER)
    cid = _real_campaign(name="admin-fraud")
    _mk_click(cid, "f_clean1", fraud="clean", eligible=1)
    _mk_click(cid, "f_self", fraud="self_view", eligible=0)
    _mk_click(cid, "f_owner", fraud="advertiser_view", eligible=0)
    fs = admin.admin_fraud_summary(cid)
    assert fs["clicks"]["total"] == 3, fs
    assert fs["clicks"]["clean"] == 1, fs
    assert fs["clicks"]["flagged"] == 2, fs
    assert fs["clicks"]["billing_eligible"] == 1, fs
    flagged = admin.admin_list_flagged_events(cid, kind="click")
    assert {e["event_id"] for e in flagged} == {"f_self", "f_owner"}, flagged


# --- governance: sensitive actions require actor + reason -------------------
def test_spend_halt_requires_actor_and_reason_and_audits():
    _approve(OWNER)
    cid = _real_campaign(name="halt")
    _force_active(cid)

    _expect_error(lambda: admin.admin_halt_spend(cid, actor="", reason="x"),
                  code="actor_required", http=400)
    _expect_error(lambda: admin.admin_halt_spend(cid, actor=ADMIN, reason="  "),
                  code="reason_required", http=400)

    before = _audit_count(admin._ACTION_SPEND_HALT)
    out = admin.admin_halt_spend(cid, actor=ADMIN, reason="Suspicious spend pattern.")
    assert out["before"]["operational_status"] == "active", out
    assert out["after"]["operational_status"] == "paused", out
    assert _audit_count(admin._ACTION_SPEND_HALT) == before + 1, "audit row written"
    lift = admin.admin_lift_spend_halt(cid, actor=ADMIN, reason="Cleared review.")
    assert lift["after"]["operational_status"] == "active", lift


def test_restrict_and_lift_advertiser_governed():
    uid = 801
    _approve(uid)
    _expect_error(lambda: admin.admin_restrict_advertiser(uid, actor=ADMIN, reason=""),
                  code="reason_required", http=400)
    out = admin.admin_restrict_advertiser(uid, actor=ADMIN, reason="Policy breach.")
    assert out["before_status"] == "approved" and out["after_status"] == "suspended", out
    # double-restrict is refused
    _expect_error(lambda: admin.admin_restrict_advertiser(
        uid, actor=ADMIN, reason="again"), code="already_restricted", http=409)
    lift = admin.admin_lift_restriction(uid, actor=ADMIN, reason="Resolved.")
    assert lift["after_status"] == "approved", lift
    # lifting a non-restricted advertiser is refused
    _expect_error(lambda: admin.admin_lift_restriction(
        uid, actor=ADMIN, reason="noop"), code="not_restricted", http=409)


# --- appeals ----------------------------------------------------------------
def test_appeal_grant_lifts_restriction_once():
    uid = 802
    _approve(uid)
    admin.admin_restrict_advertiser(uid, actor=ADMIN, reason="Under review.")
    # advertiser appeals
    _expect_error(lambda: admin.submit_appeal(uid, reason=""),
                  code="reason_required", http=400)
    appeal = admin.submit_appeal(uid, reason="I did not violate policy; please review.")
    aid = appeal["appeal_id"]
    assert aid is not None and appeal["state"] == "open", appeal
    open_appeals = admin.admin_list_appeals(user_id=uid, state="open")
    assert any(a["appeal_id"] == aid for a in open_appeals), open_appeals
    # admin resolution requires reason
    _expect_error(lambda: admin.admin_resolve_appeal(aid, "grant", actor=ADMIN,
                                                     reason=""),
                  code="reason_required", http=400)
    res = admin.admin_resolve_appeal(aid, "grant", actor=ADMIN,
                                     reason="Verified compliant.")
    assert res["decision"] == "grant" and res["restriction_lifted"] is True, res
    assert ad.get_advertiser(uid)["status"] == "approved", "restriction lifted"
    # now shows resolved, and cannot be resolved again
    resolved = admin.admin_list_appeals(user_id=uid, state="resolved")
    assert any(a["appeal_id"] == aid and a["resolution"]["decision"] == "grant"
               for a in resolved), resolved
    _expect_error(lambda: admin.admin_resolve_appeal(aid, "deny", actor=ADMIN,
                                                     reason="x"),
                  code="already_resolved", http=409)


def test_unknown_appeal_and_bad_decision():
    _expect_error(lambda: admin.admin_resolve_appeal(999999, "grant", actor=ADMIN,
                                                     reason="x"),
                  code="not_found", http=404)
    uid = 803
    _approve(uid)
    admin.admin_restrict_advertiser(uid, actor=ADMIN, reason="r")
    a = admin.submit_appeal(uid, reason="please")
    _expect_error(lambda: admin.admin_resolve_appeal(a["appeal_id"], "maybe",
                                                     actor=ADMIN, reason="x"),
                  code="bad_decision", http=400)


def _run_standalone():
    setup_module()
    tests = [
        test_billing_summary_reconciles_spend_and_escrow,
        test_fraud_summary_counts_clean_vs_flagged,
        test_spend_halt_requires_actor_and_reason_and_audits,
        test_restrict_and_lift_advertiser_governed,
        test_appeal_grant_lifts_restriction_once,
        test_unknown_appeal_and_bad_decision,
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
