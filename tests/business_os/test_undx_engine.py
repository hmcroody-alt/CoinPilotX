"""Governed UNDX actions engine (Stage 6).

Proves the deterministic governance decision projection: ingest is idempotent on
(source, external_ref); bad effect/risk are curated; an exact action_type policy beats
the '*' wildcard; priority breaks ties; a missing policy defaults to require_approval;
a risk ceiling escalates an allow to require_approval; actor permissions and emergency
stops override org policies; decisions rank deterministically
(deny < require_approval < allow, then action_type asc, then request_id asc); recompute
is a deterministic idempotent replace (no duplicate rows); and nothing beyond the four
canonical tables is created (no action executes).

    python tests/business_os/test_undx_engine.py   # no pytest needed
"""

import os
import sys
import tempfile
from datetime import datetime, timezone, timedelta

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="busos_undxeng_"), "test.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from services import db  # noqa: E402
from services.business_os.undx_actions import schema as sch  # noqa: E402
from services.business_os.undx_actions import engine as eng  # noqa: E402

_FMT = "%Y-%m-%dT%H:%M:%S.%fZ"
_BASE = datetime(2026, 7, 1, 12, 0, 0, tzinfo=timezone.utc)


def _ts(seconds=0):
    return (_BASE + timedelta(seconds=seconds)).strftime(_FMT)


def setup_module(module=None):
    sch.ensure_schema()


def _decision_for(out, request_id):
    for d in out["decisions"]:
        if d["request_id"] == request_id:
            return d
    return None


def test_policy_and_request_dedupe():
    p1 = eng.record_policy("oA", "send", "allow", source="feed", external_ref="P1")
    p2 = eng.record_policy("oA", "send", "allow", source="feed", external_ref="P1")
    assert p1["recorded"] is True and p2["deduped"] is True, (p1, p2)
    r1 = eng.record_action_request("oA", "agent", "send", source="feed",
                                   external_ref="R1")
    r2 = eng.record_action_request("oA", "agent", "send", source="feed",
                                   external_ref="R1")
    assert r1["recorded"] is True and r2["deduped"] is True, (r1, r2)


def test_bad_effect_and_risk_curated():
    for fn in (lambda: eng.record_policy("oB", "send", "sometimes"),
               lambda: eng.record_policy("oB", "send", "allow", max_risk="nuclear"),
               lambda: eng.record_action_request("oB", "a", "send", risk="spicy")):
        raised = False
        try:
            fn()
        except eng.UndxActionsError:
            raised = True
        assert raised, "invalid enum should be rejected"


def test_exact_beats_wildcard():
    eng.record_policy("oC", "*", "allow", priority=100)      # permissive default
    eng.record_policy("oC", "delete_account", "deny", priority=0)  # specific deny
    rid = eng.record_action_request("oC", "a", "delete_account")["request_id"]
    out = eng.evaluate_org("oC")
    d = _decision_for(out, rid)
    assert d["effect"] == "deny", d  # exact match wins despite lower priority


def test_priority_breaks_tie():
    eng.record_policy("oD", "post", "allow", priority=1)
    eng.record_policy("oD", "post", "deny", priority=9)
    rid = eng.record_action_request("oD", "a", "post")["request_id"]
    out = eng.evaluate_org("oD")
    assert _decision_for(out, rid)["effect"] == "deny", out


def test_default_require_approval_when_no_policy():
    rid = eng.record_action_request("oE", "a", "unknown_action")["request_id"]
    out = eng.evaluate_org("oE")
    d = _decision_for(out, rid)
    assert d["effect"] == "require_approval", d
    assert d["matched_policy_id"] is None, d


def test_risk_ceiling_escalates_allow():
    # allow up to medium risk; a high-risk request escalates to require_approval.
    eng.record_policy("oF", "send", "allow", max_risk="medium")
    low = eng.record_action_request("oF", "a", "send", risk="low")["request_id"]
    high = eng.record_action_request("oF", "a", "send", risk="high")["request_id"]
    out = eng.evaluate_org("oF")
    assert _decision_for(out, low)["effect"] == "allow", out
    assert _decision_for(out, high)["effect"] == "require_approval", out


def test_actor_permission_overrides_org_policy():
    eng.record_policy("oP", "marketplace.product.publish", "deny", priority=100)
    eng.grant_permission("oP", "seller:1", "marketplace.product.publish", "allow",
                         max_risk="medium", priority=10)
    ok = eng.record_action_request(
        "oP", "seller:1", "marketplace.product.publish", risk="low")["request_id"]
    high = eng.record_action_request(
        "oP", "seller:1", "marketplace.product.publish", risk="high")["request_id"]
    other = eng.record_action_request(
        "oP", "seller:2", "marketplace.product.publish", risk="low")["request_id"]
    out = eng.evaluate_org("oP")
    assert _decision_for(out, ok)["effect"] == "allow", out
    assert _decision_for(out, high)["effect"] == "require_approval", out
    assert _decision_for(out, other)["effect"] == "deny", out


def test_emergency_stop_overrides_permission_and_policy():
    eng.record_policy("oS", "marketplace.product.publish", "allow")
    eng.grant_permission("oS", "seller:1", "marketplace.product.publish", "allow")
    rid = eng.record_action_request(
        "oS", "seller:1", "marketplace.product.publish", risk="low")["request_id"]
    eng.activate_emergency_stop("oS", "operator", "marketplace freeze",
                                action_type="marketplace.product.publish")
    out = eng.evaluate_org("oS")
    d = _decision_for(out, rid)
    assert d["effect"] == "deny", d
    assert "emergency stop" in d["reason"], d


def test_inactive_policy_ignored():
    eng.record_policy("oG", "send", "deny", priority=5, active=False)
    rid = eng.record_action_request("oG", "a", "send")["request_id"]
    out = eng.evaluate_org("oG")
    # inactive deny skipped -> falls through to default require_approval
    assert _decision_for(out, rid)["effect"] == "require_approval", out


def test_deterministic_ordering():
    eng.record_policy("oH", "z_allow", "allow")
    eng.record_policy("oH", "a_deny", "deny")
    eng.record_policy("oH", "m_appr", "require_approval")
    eng.record_action_request("oH", "a", "z_allow", external_ref="H_allow",
                              source="s")
    eng.record_action_request("oH", "a", "a_deny", external_ref="H_deny", source="s")
    eng.record_action_request("oH", "a", "m_appr", external_ref="H_appr", source="s")
    out = eng.evaluate_org("oH")
    effects = [d["effect"] for d in out["decisions"]]
    assert effects == ["deny", "require_approval", "allow"], effects
    ranks = [d["rank"] for d in out["decisions"]]
    assert ranks == [1, 2, 3], ranks


def test_recompute_idempotent_replace():
    eng.record_policy("oR", "send", "allow")
    eng.record_action_request("oR", "a", "send", external_ref="RR1", source="s")
    eng.record_action_request("oR", "b", "send", external_ref="RR2", source="s")
    first = eng.evaluate_org("oR")
    second = eng.evaluate_org("oR")
    assert first["decisions"] == second["decisions"], (first, second)
    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT request_id, COUNT(*) c FROM business_os_undx_decisions "
            "WHERE org_id = ? GROUP BY request_id", ("oR",)).fetchall()
        for r in rows:
            assert dict(r)["c"] == 1, dict(r)
    finally:
        conn.close()


def test_no_side_effects():
    eng.record_policy("oN", "send", "allow")
    eng.record_action_request("oN", "a", "send")
    eng.evaluate_org("oN")
    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name LIKE 'business_os_undx_%'").fetchall()
        names = {r[0] for r in rows}
        assert names == {
            "business_os_undx_policies",
            "business_os_undx_action_requests",
            "business_os_undx_decisions",
            "business_os_undx_audit",
            "business_os_undx_tool_registry",
            "business_os_undx_permissions",
            "business_os_undx_confirmations",
            "business_os_undx_action_receipts",
            "business_os_undx_emergency_stops"}, names
    finally:
        conn.close()


def test_registry_receipt_and_action_center():
    tool = eng.register_tool(
        "marketplace.create_product", "marketplace.product.create",
        product_area="marketplace", risk="low")
    req = eng.record_action_request(
        "oAC", "seller:1", "marketplace.product.create", risk="low")
    conf = eng.record_confirmation(
        "oAC", req["request_id"], "seller:1", "hash:abc", status="confirmed")
    receipt = eng.record_receipt(
        "oAC", "marketplace.product.create", "seller:1", "verified",
        request_id=req["request_id"], canonical_ref="product:p1",
        verification={"status": "draft"})
    center = eng.action_center("oAC")
    assert tool["tool_id"], tool
    assert conf["status"] == "confirmed", conf
    assert receipt["status"] == "verified", receipt
    assert any(r["request_id"] == req["request_id"] for r in center["requests"]), center
    assert any(r["canonical_ref"] == "product:p1" for r in center["receipts"]), center


def test_confirmation_redemption_is_bound_single_use_and_expiring():
    req = eng.record_action_request(
        "oConfirm", "seller:1", "marketplace.product.publish",
        params={"product_id": "p1"})
    pending = eng.record_confirmation(
        "oConfirm", req["request_id"], "seller:1", "payload:p1",
        expires_at=(datetime.now(timezone.utc) + timedelta(hours=1)).strftime(_FMT))

    for args in (
        ("other", req["request_id"], "seller:1", "payload:p1"),
        ("oConfirm", req["request_id"], "seller:2", "payload:p1"),
        ("oConfirm", req["request_id"], "seller:1", "payload:p2"),
    ):
        try:
            eng.redeem_confirmation(*args)
            assert False, f"misbound confirmation should fail: {args}"
        except eng.UndxActionsError:
            pass

    redeemed = eng.redeem_confirmation(
        "oConfirm", req["request_id"], "seller:1", "payload:p1")
    assert redeemed["confirmation_id"] == pending["confirmation_id"], redeemed
    assert redeemed["status"] == "confirmed", redeemed
    try:
        eng.redeem_confirmation(
            "oConfirm", req["request_id"], "seller:1", "payload:p1")
        assert False, "confirmation replay should fail"
    except eng.UndxActionsError as exc:
        assert "no longer pending" in str(exc), exc

    expired_req = eng.record_action_request(
        "oConfirm", "seller:1", "marketplace.product.publish",
        params={"product_id": "p2"})
    expired = eng.record_confirmation(
        "oConfirm", expired_req["request_id"], "seller:1", "payload:p2",
        expires_at="2000-01-01T00:00:00.000000Z")
    try:
        eng.redeem_confirmation(
            "oConfirm", expired_req["request_id"], "seller:1", "payload:p2")
        assert False, "expired confirmation should fail"
    except eng.UndxActionsError as exc:
        assert "expired" in str(exc), exc
    center = eng.action_center("oConfirm")
    expired_row = next(
        row for row in center["confirmations"]
        if row["confirmation_id"] == expired["confirmation_id"])
    assert expired_row["status"] == "expired", expired_row


def _run_standalone():
    setup_module()
    tests = [
        test_policy_and_request_dedupe,
        test_bad_effect_and_risk_curated,
        test_exact_beats_wildcard,
        test_priority_breaks_tie,
        test_default_require_approval_when_no_policy,
        test_risk_ceiling_escalates_allow,
        test_actor_permission_overrides_org_policy,
        test_emergency_stop_overrides_permission_and_policy,
        test_inactive_policy_ignored,
        test_deterministic_ordering,
        test_recompute_idempotent_replace,
        test_no_side_effects,
        test_registry_receipt_and_action_center,
        test_confirmation_redemption_is_bound_single_use_and_expiring,
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
