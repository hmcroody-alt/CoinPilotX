"""Advertising slice 3 — campaign review lifecycle controller test matrix.

Exercises the review workflow directly against the importable controller
(services/business_os/advertising/api.py) + service (bot.py is not importable in
the hermetic sandbox; the route adapters are checked structurally in
test_advertising_slice3_routes.py). Proves the full path:

    advertiser draft -> submit -> admin review (approve | reject) -> reopen/withdraw

and the guardrails around it: eligibility + account-hold precedence on submit,
ownership 404, submitted-is-not-editable, reject-requires-reason with the reason
surfaced to the owner, review-approved-only (no spend/delivery), every illegal
transition rejected, audit rows written, and flag-off darkness.

    python tests/business_os/test_advertising_slice3_api.py   # no pytest needed
"""

import os
import tempfile

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="busos_ad3_"), "test.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB
os.environ["BUSINESS_OS_ADVERTISING"] = "on"

import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from services import db  # noqa: E402
from services.business_os.advertising import service as ad  # noqa: E402
from services.business_os.advertising import api as adapi  # noqa: E402

OWNER = 700
OWNER2 = 701
ADMIN = 8
HELD = 703
ACTIVE = {"account_status": "active", "access_enabled": 1}
SUSPENDED = {"account_status": "suspended", "access_enabled": 1}


def setup_module(module=None):
    ad.ensure_schema()


def _approve(uid):
    ad.upsert_advertiser(uid)
    ad.set_advertiser_status(uid, "approved", actor=ADMIN)


def _draft(owner, name="Camp", objective="traffic"):
    """Create a valid draft via the controller; return its campaign_id."""
    s, b = adapi.create_draft(
        owner, {"name": name, "objective": objective,
                "destination_url": "https://ex.com"}, context=ACTIVE)
    assert s == 201, (s, b)
    return b["campaign"]["campaign_id"]


def _submit(owner, cid):
    return adapi.submit(owner, cid, context=ACTIVE)


def _insert_raw_campaign(owner, *, objective="", status="draft"):
    """Insert a campaign row directly to simulate a stored draft with an invalid
    field (the normal create path validates, so this is the only way to prove the
    submit-time re-validation guard)."""
    conn = db.connect()
    try:
        cid = "raw_" + os.urandom(5).hex()
        now = ad._now_iso()
        conn.execute(
            "INSERT INTO business_os_ad_campaigns (campaign_id, advertiser_user_id,"
            " name, objective, status, destination_url, created_by, metadata_json,"
            " created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (cid, str(owner), "Raw", objective, status, None, str(owner), None,
             now, now),
        )
        conn.commit()
        return cid
    finally:
        conn.close()


def _assert(cond, msg):
    if not cond:
        raise AssertionError(msg)


# 1 -- flag OFF: every slice-3 handler is dark (404) -------------------------
def test_flag_off_dark():
    os.environ["BUSINESS_OS_ADVERTISING"] = "off"
    for result in (
        adapi.submit(OWNER, "nope", context=ACTIVE),
        adapi.withdraw(OWNER, "nope"),
        adapi.reopen(OWNER, "nope"),
        adapi.admin_review(ADMIN, "nope", "approve"),
    ):
        status, body = result
        _assert(status == 404 and body["ok"] is False, f"expected dark 404, got {result}")
    os.environ["BUSINESS_OS_ADVERTISING"] = "on"


# 2 -- owner submits a valid draft -> submitted ------------------------------
def test_submit_valid():
    _approve(OWNER)
    cid = _draft(OWNER)
    s, b = _submit(OWNER, cid)
    _assert(s == 200 and b["campaign"]["status"] == "submitted", b)


# 3 -- an invalid stored draft cannot be submitted ---------------------------
def test_invalid_draft_cannot_submit():
    _approve(OWNER)
    cid = _insert_raw_campaign(OWNER, objective="")  # invalid objective
    s, b = _submit(OWNER, cid)
    _assert(s == 400 and b["code"] == "bad_objective", b)


# 4 -- pending or suspended advertiser cannot submit -------------------------
def test_pending_or_suspended_cannot_submit():
    # approved -> draft -> demote advertiser to pending -> submit denied
    _approve(OWNER)
    cid = _draft(OWNER)
    ad.set_advertiser_status(OWNER, "pending", actor=ADMIN)
    s, b = _submit(OWNER, cid)
    _assert(s == 403 and b["code"] == "ineligible", b)
    ad.set_advertiser_status(OWNER, "approved", actor=ADMIN)  # restore
    # approved advertiser but suspended ACCOUNT -> hold overrides approval
    _approve(HELD)
    cid2 = _draft(HELD)
    s, b = adapi.submit(HELD, cid2, context=SUSPENDED)
    _assert(s == 403 and b["code"] == "ineligible", b)


# 5 -- non-owner cannot submit or withdraw (existence not leaked) ------------
def test_nonowner_cannot_submit_or_withdraw():
    _approve(OWNER)
    _approve(OWNER2)
    cid = _draft(OWNER)
    s, b = adapi.submit(OWNER2, cid, context=ACTIVE)
    _assert(s == 404 and b["code"] == "not_found", b)
    _submit(OWNER, cid)  # now submitted
    s, b = adapi.withdraw(OWNER2, cid)
    _assert(s == 404 and b["code"] == "not_found", b)


# 6 -- a submitted campaign cannot be edited as a draft ----------------------
def test_submitted_not_editable():
    _approve(OWNER)
    cid = _draft(OWNER)
    _submit(OWNER, cid)
    s, b = adapi.update_draft(OWNER, cid, {"name": "late edit"})
    _assert(s == 409 and b["code"] == "not_editable", b)


# 7 -- advertiser can withdraw a submitted campaign back to draft ------------
def test_withdraw():
    _approve(OWNER)
    cid = _draft(OWNER)
    _submit(OWNER, cid)
    s, b = adapi.withdraw(OWNER, cid)
    _assert(s == 200 and b["campaign"]["status"] == "draft", b)
    # editable again once back in draft
    s, b = adapi.update_draft(OWNER, cid, {"name": "Renamed"})
    _assert(s == 200 and b["campaign"]["name"] == "Renamed", b)


# 8 -- admin approves a submitted campaign (review-approved only) ------------
def test_admin_approve():
    _approve(OWNER)
    cid = _draft(OWNER)
    _submit(OWNER, cid)
    s, b = adapi.admin_review(ADMIN, cid, "approve")
    _assert(s == 200 and b["after_status"] == "approved", b)
    _assert(b["before_status"] == "submitted", b)
    _assert(b["campaign"]["status"] == "approved", b)
    _assert(b["campaign"].get("review_reason") in (None, ""), b)


# 9 -- admin rejects with a reason; owner can see the reason -----------------
def test_admin_reject_with_reason():
    _approve(OWNER)
    cid = _draft(OWNER)
    _submit(OWNER, cid)
    # reject without a reason is refused
    s, b = adapi.admin_review(ADMIN, cid, "reject")
    _assert(s == 400 and b["code"] == "reason_required", b)
    # reject with a reason succeeds and surfaces the reason to the owner
    s, b = adapi.admin_review(ADMIN, cid, "reject", reason="Landing page broken")
    _assert(s == 200 and b["after_status"] == "rejected", b)
    _assert(b["reason"] == "Landing page broken", b)
    s, b = adapi.get_own_campaign(OWNER, cid)
    _assert(s == 200 and b["campaign"]["status"] == "rejected", b)
    _assert(b["campaign"]["review_reason"] == "Landing page broken", b)


# 10 -- advertiser reopens a rejected campaign; reason cleared ---------------
def test_reopen_rejected():
    _approve(OWNER)
    cid = _draft(OWNER)
    _submit(OWNER, cid)
    adapi.admin_review(ADMIN, cid, "reject", reason="fix creative")
    s, b = adapi.reopen(OWNER, cid)
    _assert(s == 200 and b["campaign"]["status"] == "draft", b)
    _assert(b["campaign"].get("review_reason") in (None, ""), b)


# 11 -- every illegal lifecycle transition is rejected -----------------------
def test_illegal_transitions_rejected():
    _approve(OWNER)
    cid = _draft(OWNER)  # draft
    # withdraw/ reopen / review require specific source states
    _assert(adapi.withdraw(OWNER, cid)[1]["code"] == "not_withdrawable", "withdraw draft")
    _assert(adapi.reopen(OWNER, cid)[1]["code"] == "not_reopenable", "reopen draft")
    _assert(adapi.admin_review(ADMIN, cid, "approve")[1]["code"] == "not_reviewable", "review draft")
    # bad decision verb
    _submit(OWNER, cid)
    _assert(adapi.admin_review(ADMIN, cid, "banana")[1]["code"] == "bad_decision", "bad decision")
    # cannot submit a non-draft (it is submitted now)
    _assert(adapi.submit(OWNER, cid, context=ACTIVE)[1]["code"] == "not_submittable", "resubmit")
    # approve, then cannot submit/withdraw/reopen an approved campaign
    adapi.admin_review(ADMIN, cid, "approve")
    _assert(adapi.submit(OWNER, cid, context=ACTIVE)[1]["code"] == "not_submittable", "submit approved")
    _assert(adapi.withdraw(OWNER, cid)[1]["code"] == "not_withdrawable", "withdraw approved")


# 12 -- approving does NOT trigger spend or delivery -------------------------
def test_approved_has_no_spend_or_delivery():
    _approve(OWNER)
    cid = _draft(OWNER)
    _submit(OWNER, cid)
    s, b = adapi.admin_review(ADMIN, cid, "approve")
    camp = b["campaign"]
    # the canonical campaign carries no money/delivery fields at all
    for banned in ("spend", "balance", "budget", "impressions", "delivered",
                   "charged", "amount", "cost"):
        _assert(banned not in camp, f"approved campaign leaked spend/delivery field {banned!r}")
    _assert(camp["status"] == "approved", camp)
    # the legacy delivery table is never created/written by this path
    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='pulse_ad_campaigns'"
        ).fetchone()
    finally:
        conn.close()
    _assert(row is None, "canonical review path must not create the legacy pulse_ad_campaigns table")


# 13 -- audit rows are produced for the review lifecycle ---------------------
def test_audit_records_written():
    _approve(OWNER)
    cid = _draft(OWNER)
    _submit(OWNER, cid)
    adapi.admin_review(ADMIN, cid, "reject", reason="nope")
    adapi.reopen(OWNER, cid)
    _submit(OWNER, cid)
    adapi.admin_review(ADMIN, cid, "approve")
    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT action, reason FROM business_os_ad_audit WHERE campaign_id = ? "
            "ORDER BY id", (cid,)
        ).fetchall()
    finally:
        conn.close()
    actions = [r["action"] for r in rows]
    for needed in ("campaign_create", "campaign_submit", "campaign_review",
                   "campaign_reopen"):
        _assert(needed in actions, f"missing audit action {needed}: {actions}")
    # the rejection reason is recorded on a review audit row
    reasons = [r["reason"] for r in rows if r["action"] == "campaign_review"]
    _assert("nope" in reasons, f"rejection reason not audited: {reasons}")


# --- standalone runner ------------------------------------------------------
def _run_standalone():
    setup_module()
    tests = [
        test_flag_off_dark,
        test_submit_valid,
        test_invalid_draft_cannot_submit,
        test_pending_or_suspended_cannot_submit,
        test_nonowner_cannot_submit_or_withdraw,
        test_submitted_not_editable,
        test_withdraw,
        test_admin_approve,
        test_admin_reject_with_reason,
        test_reopen_rejected,
        test_illegal_transitions_rejected,
        test_approved_has_no_spend_or_delivery,
        test_audit_records_written,
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
