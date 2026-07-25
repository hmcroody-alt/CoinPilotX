"""Governed UNDX actions controller contract (Stage 6).

Proves the framework-agnostic contract: DARK (404) when the flag is off; missing
payload/fields -> 400 with curated codes; recording a policy + action request;
decisions report is computed-on-read; policies/requests reports; evaluate runs. Curated
codes only, never a raw exception.

    python tests/business_os/test_undx_api.py   # no pytest needed
"""

import os
import sys
import tempfile

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="busos_undxapi_"), "test.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB
os.environ["BUSINESS_OS_UNDX_ACTIONS"] = "on"

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from services.business_os.undx_actions import schema as sch  # noqa: E402
from services.business_os.undx_actions import api  # noqa: E402


def setup_module(module=None):
    sch.ensure_schema()


# --- (a) dark when disabled -------------------------------------------------
def test_dark_when_disabled():
    os.environ["BUSINESS_OS_UNDX_ACTIONS"] = "0"
    try:
        assert api.record_policy({})[0] == 404
        assert api.record_action_request({})[0] == 404
        assert api.decisions_report("o")[0] == 404
        assert api.policies_report("o")[0] == 404
        assert api.requests_report("o")[0] == 404
        assert api.register_tool({})[0] == 404
        assert api.grant_permission({})[0] == 404
        assert api.record_confirmation({})[0] == 404
        assert api.record_receipt({})[0] == 404
        assert api.activate_emergency_stop({})[0] == 404
        assert api.action_center_report("o")[0] == 404
        assert api.run_evaluate("o")[0] == 404
    finally:
        os.environ["BUSINESS_OS_UNDX_ACTIONS"] = "on"


# --- (b) validation ---------------------------------------------------------
def test_policy_missing_fields():
    st, body = api.record_policy({"org_id": "o", "action_type": "send"})
    assert st == 400 and body["code"] == "missing_fields", body


def test_policy_bad_effect_curated():
    st, body = api.record_policy({"org_id": "o", "action_type": "send",
                                  "effect": "maybe"})
    assert st == 400 and body["code"] == "invalid_policy", body


def test_request_missing_fields():
    st, body = api.record_action_request({"org_id": "o", "actor": "a"})
    assert st == 400 and body["code"] == "missing_fields", body


def test_request_bad_risk_curated():
    st, body = api.record_action_request({"org_id": "o", "actor": "a",
                                          "action_type": "send", "risk": "spicy"})
    assert st == 400 and body["code"] == "invalid_request", body


def test_decisions_missing_org():
    st, body = api.decisions_report("")
    assert st == 400 and body["code"] == "missing_fields", body


# --- (c) record + compute-on-read decisions ---------------------------------
def test_decisions_computed_on_read():
    api.record_policy({"org_id": "O1", "action_type": "delete_account",
                       "effect": "deny", "priority": 5})
    api.record_policy({"org_id": "O1", "action_type": "send", "effect": "allow"})
    api.record_action_request({"org_id": "O1", "actor": "agent",
                               "action_type": "delete_account"})
    api.record_action_request({"org_id": "O1", "actor": "agent",
                               "action_type": "send"})
    api.record_action_request({"org_id": "O1", "actor": "agent",
                               "action_type": "mystery"})  # no policy -> approval
    st, body = api.decisions_report("O1")
    assert st == 200, body
    by_action = {d["action_type"]: d["effect"] for d in body["result"]["decisions"]}
    assert by_action["delete_account"] == "deny", by_action
    assert by_action["send"] == "allow", by_action
    assert by_action["mystery"] == "require_approval", by_action


# --- (d) policies + requests reports + evaluate -----------------------------
def test_policies_and_requests_reports():
    st, body = api.policies_report("O1")
    assert st == 200 and any(p["effect"] == "deny"
                             for p in body["result"]["policies"]), body
    st2, b2 = api.requests_report("O1")
    assert st2 == 200 and any(r["action_type"] == "send"
                              for r in b2["result"]["requests"]), b2


def test_evaluate_runs():
    st, body = api.run_evaluate("O1")
    assert st == 200 and "decisions" in body["result"], body
    st2, b2 = api.run_evaluate("")
    assert st2 == 400 and b2["code"] == "missing_fields", b2


def test_governance_foundation_surfaces():
    st, body = api.register_tool({
        "tool_name": "marketplace.create_product",
        "action_type": "marketplace.product.create",
        "product_area": "marketplace",
        "risk": "low",
    })
    assert st == 200 and body["result"]["tool_id"], body
    st2, b2 = api.grant_permission({
        "org_id": "O2",
        "actor": "seller:1",
        "action_type": "marketplace.product.create",
        "effect": "allow",
    })
    assert st2 == 200 and b2["result"]["permission_id"], b2
    st3, b3 = api.record_action_request({
        "org_id": "O2",
        "actor": "seller:1",
        "action_type": "marketplace.product.create",
        "risk": "low",
    })
    assert st3 == 200, b3
    request_id = b3["result"]["request_id"]
    st4, b4 = api.record_confirmation({
        "org_id": "O2",
        "request_id": request_id,
        "actor": "seller:1",
        "payload_hash": "hash:listing",
        "status": "confirmed",
    })
    assert st4 == 200 and b4["result"]["status"] == "confirmed", b4
    st5, b5 = api.record_receipt({
        "org_id": "O2",
        "request_id": request_id,
        "action_type": "marketplace.product.create",
        "actor": "seller:1",
        "status": "verified",
        "canonical_ref": "product:p1",
        "verification": {"status": "draft"},
    })
    assert st5 == 200 and b5["result"]["status"] == "verified", b5
    api.run_evaluate("O2")
    st6, b6 = api.action_center_report("O2")
    assert st6 == 200, b6
    assert any(r["canonical_ref"] == "product:p1"
               for r in b6["result"]["receipts"]), b6


def test_emergency_stop_denies_action_center_decision():
    api.record_policy({"org_id": "O3", "action_type": "marketplace.product.publish",
                       "effect": "allow"})
    st, body = api.record_action_request({
        "org_id": "O3",
        "actor": "seller:1",
        "action_type": "marketplace.product.publish",
        "risk": "high",
    })
    assert st == 200, body
    api.activate_emergency_stop({
        "org_id": "O3",
        "actor": "operator",
        "action_type": "marketplace.product.publish",
        "reason": "release freeze",
    })
    st2, b2 = api.run_evaluate("O3")
    assert st2 == 200, b2
    effects = {d["action_type"]: d["effect"] for d in b2["result"]["decisions"]}
    assert effects["marketplace.product.publish"] == "deny", effects


def _run_standalone():
    setup_module()
    tests = [
        test_dark_when_disabled,
        test_policy_missing_fields,
        test_policy_bad_effect_curated,
        test_request_missing_fields,
        test_request_bad_risk_curated,
        test_decisions_missing_org,
        test_decisions_computed_on_read,
        test_policies_and_requests_reports,
        test_evaluate_runs,
        test_governance_foundation_surfaces,
        test_emergency_stop_denies_action_center_decision,
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
