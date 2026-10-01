"""Advertising Stage 2 — canonical notification wiring matrix.

Proves that advertising lifecycle/billing transitions EMIT the canonical alert
through the existing notification path (spec Part 3: "wire canonical notifications
... use existing notification system — no competing system"), and that the emit is
a pure side effect that never becomes a precondition of the money/lifecycle
decision.

Two things are proven here:

  1. **Content is server-derived and correct** — ``build_notification`` produces the
     right category/title/body/deep-link/priority for every canonical type, ratios
     and money are formatted from the ids the caller passed, and an unknown type is
     rejected (a typo is caught, not silently swallowed).
  2. **Emits fire at the real transition points** — with a captured sender injected
     via ``set_sender``, an admin campaign approve/reject fires
     ``campaign_approved``/``campaign_rejected``; a budget-exhausting billing charge
     fires ``budget_exhausted`` + ``billing_failure`` exactly ONCE on the 0->1 latch
     transition and NOT again on a later refused event; and a delivery failure inside
     the sender is swallowed (``ok=False``) instead of raising into billing.

bot.py is not importable in the hermetic sandbox; route adapters are checked
structurally elsewhere.

    python tests/business_os/test_advertising_notifications.py   # no pytest needed
"""

import os
import tempfile
import uuid
import datetime

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="busos_adnotif_"), "test.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB
os.environ["BUSINESS_OS_ADVERTISING"] = "on"

import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from services import db  # noqa: E402
from services.business_os.advertising import schema as ad_schema  # noqa: E402
from services.business_os.advertising import service as ad  # noqa: E402
from services.business_os.advertising import pricing, billing, notifications  # noqa: E402
from services.business_os.ledger import ledger  # noqa: E402


OWNER = 700
ADMIN = 9
ESCROW = "ad_campaign_escrow:"


# --- captured sender --------------------------------------------------------
_CAPTURED = []


def _capturing_sender(user_id, category, title, body, data=None, priority=None):
    _CAPTURED.append({
        "user_id": user_id, "category": category, "title": title, "body": body,
        "data": data or {}, "priority": priority,
    })
    return {"delivered": True}


def _reset():
    _CAPTURED.clear()


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
    notifications.set_sender(_capturing_sender)


def teardown_module(module=None):
    notifications.set_sender(None)  # restore the orchestrator


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


def _mk_click(cid, eid, eligible=1):
    conn = db.connect()
    try:
        conn.execute(
            "INSERT INTO business_os_ad_click_events "
            "(event_id, delivery_id, impression_event_id, destination_type, "
            "destination_ref, campaign_id, ad_set_id, creative_id, creative_version, "
            "placement, subject_ref, advertiser_user_id, event_at, dedup_key, "
            "fraud_status, billing_eligible, billing_processed, created_at) "
            "VALUES (?, ?, ?, 'url', 'https://x', ?, 'as1', 'cr1', 1, 'feed', "
            "'viewer1', ?, ?, ?, 'clean', ?, 0, ?)",
            (eid, "dlv_" + eid, "imp_" + eid, cid, OWNER, _now(),
             "dk_" + eid, eligible, _now()))
        conn.commit()
    finally:
        conn.close()


def _types():
    return [c["data"].get("notif_type") for c in _CAPTURED]


# --- content (pure builder) -------------------------------------------------
def test_build_notification_content_and_priority():
    p = notifications.build_notification("campaign_rejected", campaign_id="c1",
                                         reason="Policy violation.")
    assert p["category"] == "advertising", p
    assert p["priority"] == "high", p
    assert p["deep_link"] == "/ads/campaigns/c1", p
    assert "Policy violation." in p["body"], p
    # creative deep link carries both ids
    pc = notifications.build_notification("creative_approved", campaign_id="c1",
                                          creative_id="cr9")
    assert pc["deep_link"] == "/ads/campaigns/c1/creatives/cr9", pc
    assert pc["priority"] == "normal", pc
    # budget-approaching formats money + pct from server ids
    pb = notifications.build_notification("budget_approaching", campaign_id="c1",
                                          remaining_cents=1234, pct_spent=90)
    assert "$12.34" in pb["body"], pb
    assert "90%" in pb["body"], pb


def test_unknown_type_is_rejected():
    try:
        notifications.build_notification("not_a_real_type", campaign_id="c1")
    except ValueError:
        return
    raise AssertionError("expected ValueError for an unknown notification type")


# --- lifecycle: admin review fires approved / rejected ----------------------
def test_admin_approve_emits_campaign_approved():
    _reset()
    _approve(OWNER)
    c = ad.create_campaign_draft(OWNER, name="NL approve", objective="traffic",
                                 context=_ctx())
    cid = c["campaign_id"]
    ad.submit_campaign(cid, requester_user_id=OWNER, context=_ctx())
    out = ad.admin_review_campaign(cid, "approve", actor=ADMIN)
    assert out["after_status"] == "approved", out
    assert _types() == ["campaign_approved"], _CAPTURED
    got = _CAPTURED[0]
    assert str(got["user_id"]) == str(OWNER), got
    assert got["data"]["deep_link"] == f"/ads/campaigns/{cid}", got


def test_admin_reject_emits_campaign_rejected_with_reason():
    _reset()
    _approve(OWNER)
    c = ad.create_campaign_draft(OWNER, name="NL reject", objective="traffic",
                                 context=_ctx())
    cid = c["campaign_id"]
    ad.submit_campaign(cid, requester_user_id=OWNER, context=_ctx())
    ad.admin_review_campaign(cid, "reject", actor=ADMIN, reason="Missing disclosure.")
    assert _types() == ["campaign_rejected"], _CAPTURED
    assert "Missing disclosure." in _CAPTURED[0]["body"], _CAPTURED
    assert _CAPTURED[0]["priority"] == "high", _CAPTURED


# --- billing: budget exhaustion fires once on the latch transition ----------
def test_budget_exhaustion_emits_once_then_is_quiet():
    _reset()
    cid = "camp_notif_broke"
    _fund(cid, 10)                 # only 10c; a 25c click cannot post
    _mk_click(cid, "clk_a")
    _mk_click(cid, "clk_b")
    r1 = billing.bill_event("click", "clk_a")
    assert r1["billing_status"] == "failed", r1
    assert ledger.get_balance(ESCROW + cid) == 10, "no overdraft"
    # first exhaustion: both alerts fire exactly once
    assert _types() == ["budget_exhausted", "billing_failure"], _CAPTURED
    assert billing.budget_exhausted(cid) is True
    # a SECOND refused event must NOT re-alert (latch already 1)
    _reset()
    r2 = billing.bill_event("click", "clk_b")
    assert r2["billing_status"] == "failed", r2
    assert _types() == [], _CAPTURED  # quiet on subsequent refusals


# --- delivery failure never breaks the caller -------------------------------
def test_delivery_failure_is_swallowed_not_raised():
    def _boom(*a, **k):
        raise RuntimeError("channel down")
    notifications.set_sender(_boom)
    try:
        res = notifications.notify_campaign_approved(OWNER, "c_any")
        assert res["ok"] is False, res
        assert res["status"] == "delivery_error", res
    finally:
        notifications.set_sender(_capturing_sender)  # restore capture


def _run_standalone():
    setup_module()
    tests = [
        test_build_notification_content_and_priority,
        test_unknown_type_is_rejected,
        test_admin_approve_emits_campaign_approved,
        test_admin_reject_emits_campaign_rejected_with_reason,
        test_budget_exhaustion_emits_once_then_is_quiet,
        test_delivery_failure_is_swallowed_not_raised,
    ]
    passed = 0
    try:
        for t in tests:
            t()
            print(f"PASS  {t.__name__}")
            passed += 1
    finally:
        teardown_module()
    print(f"\n{passed}/{len(tests)} tests passed")
    return passed == len(tests)


if __name__ == "__main__":
    ok = _run_standalone()
    raise SystemExit(0 if ok else 1)
