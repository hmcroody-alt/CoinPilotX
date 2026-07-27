"""Advertising Stage 2 — HTTP controller wiring for the new surfaces (Part 5 of the
consolidated sprint): report/spend (advertiser), the governed assistant, and the
Part-6 admin surfaces (billing inspection, fraud, spend controls, restrictions,
appeals).

Every handler returns ``(status_code, body)`` with an ``ok`` bool, is DARK (404)
when the flag is off, surfaces only curated AdvertisingError messages, and enforces
ownership on advertiser-facing reads. Governed admin actions require a reason.

    python tests/business_os/test_advertising_stage2_api.py   # no pytest needed
"""

import os
import tempfile

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="busos_stage2api_"), "test.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB
os.environ["BUSINESS_OS_ADVERTISING"] = "on"

import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from services import db  # noqa: E402
from services.business_os.advertising import schema as ad_schema  # noqa: E402
from services.business_os.advertising import service as ad  # noqa: E402
from services.business_os.advertising import pricing  # noqa: E402
from services.business_os.advertising import api  # noqa: E402
from services.business_os.ledger import ledger  # noqa: E402


OWNER = 900
OTHER = 901
ADMIN = 9


def setup_module(module=None):
    ad_schema.ensure_schema()
    ledger.ensure_schema()
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


def _draft(uid, name="c"):
    return ad.create_campaign_draft(uid, name=name, objective="traffic",
                                    context=_ctx())["campaign_id"]


# --- advertiser report + spend, owner-scoped --------------------------------
def test_report_and_spend_owner_scoped():
    _approve(OWNER)
    cid = _draft(OWNER, "report")
    st, body = api.get_campaign_report(OWNER, cid, {"currency": "usd"})
    assert st == 200 and body["ok"] and "report" in body, body
    st, body = api.get_campaign_spend(OWNER, cid, None)
    assert st == 200 and body["ok"] and "spend" in body, body
    # a non-owner is refused with 404 (existence not leaked)
    st, body = api.get_campaign_report(OTHER, cid, None)
    assert st == 404 and body["ok"] is False, body
    st, body = api.get_campaign_spend(OTHER, cid, None)
    assert st == 404 and body["ok"] is False, body
    # unknown field rejected
    st, body = api.get_campaign_report(OWNER, cid, {"bogus": 1})
    assert st == 400 and body["code"] == "unknown_field", body


# --- assistant plan/execute wiring ------------------------------------------
def test_assistant_plan_execute_wiring():
    _approve(OWNER)
    cid = _draft(OWNER, "asst")
    st, body = api.assistant_list_tools(OWNER)
    assert st == 200 and any(t["tool"] == "set_budget" for t in body["tools"]), body
    # read tool runs from plan without confirmation
    st, body = api.assistant_plan(
        OWNER, {"tool": "operational_status", "params": {"campaign_id": cid}})
    assert st == 200 and body["plan"]["requires_confirmation"] is False, body
    # consequential tool: plan mints token, execute without it is 428
    st, plan = api.assistant_plan(
        OWNER, {"tool": "set_budget",
                "params": {"campaign_id": cid, "budget_cents": 5000,
                           "currency": "usd"}})
    token = plan["plan"]["confirmation_token"]
    assert plan["plan"]["requires_confirmation"] is True, plan
    st, body = api.assistant_execute(
        OWNER, {"tool": "set_budget",
                "params": {"campaign_id": cid, "budget_cents": 5000,
                           "currency": "usd"}})
    assert st == 428 and body["code"] == "confirmation_required", body
    # with the right token it executes and is verified
    st, body = api.assistant_execute(
        OWNER, {"tool": "set_budget",
                "params": {"campaign_id": cid, "budget_cents": 5000,
                           "currency": "usd"},
                "confirmation_token": token})
    assert st == 200 and body["result"]["verified"] is True, body
    # missing tool -> 400
    st, body = api.assistant_plan(OWNER, {"params": {}})
    assert st == 400 and body["code"] == "tool_required", body


# --- admin billing + fraud reads --------------------------------------------
def test_admin_billing_and_fraud_reads():
    _approve(OWNER)
    cid = _draft(OWNER, "adminread")
    st, body = api.admin_billing_summary(cid, {"currency": "usd"})
    assert st == 200 and body["billing"]["campaign_id"] == cid, body
    st, body = api.admin_fraud_summary(cid)
    assert st == 200 and "impressions" in body["fraud"], body
    st, body = api.admin_list_billing_events(campaign_id=cid)
    assert st == 200 and body["ok"], body


# --- governed admin actions require reason ----------------------------------
def test_admin_governed_actions_require_reason():
    uid = 902
    _approve(uid)
    # restrict without reason -> 400
    st, body = api.admin_restrict_advertiser(ADMIN, uid, {})
    assert st == 400 and body["code"] == "reason_required", body
    # with reason -> before/after
    st, body = api.admin_restrict_advertiser(ADMIN, uid, {"reason": "Policy breach."})
    assert st == 200 and body["after_status"] == "suspended", body
    # advertiser appeals, admin grants -> restriction lifted
    st, body = api.submit_appeal(uid, {"reason": "Please review."})
    assert st == 200, body
    aid = body["appeal"]["appeal_id"]
    st, body = api.admin_resolve_appeal(ADMIN, aid, {"decision": "grant",
                                                     "reason": "Compliant."})
    assert st == 200 and body["restriction_lifted"] is True, body
    assert ad.get_advertiser(uid)["status"] == "approved", "lifted"


# --- dark when flag off -----------------------------------------------------
def test_dark_when_flag_off():
    os.environ["BUSINESS_OS_ADVERTISING"] = "off"
    try:
        for call in (
            lambda: api.get_campaign_report(OWNER, "x", None),
            lambda: api.assistant_list_tools(OWNER),
            lambda: api.assistant_plan(OWNER, {"tool": "report"}),
            lambda: api.admin_billing_summary("x"),
            lambda: api.admin_restrict_advertiser(ADMIN, 1, {"reason": "r"}),
        ):
            st, body = call()
            assert st == 404 and body["ok"] is False, (st, body)
    finally:
        os.environ["BUSINESS_OS_ADVERTISING"] = "on"


def _run_standalone():
    setup_module()
    tests = [
        test_report_and_spend_owner_scoped,
        test_assistant_plan_execute_wiring,
        test_admin_billing_and_fraud_reads,
        test_admin_governed_actions_require_reason,
        test_dark_when_flag_off,
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
