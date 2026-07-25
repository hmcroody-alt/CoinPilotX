"""Advertising slice 5 — controlled activation + scheduling controller matrix.

Exercises the operational lifecycle directly against the importable controller
(services/business_os/advertising/api.py) + operations service + the slice-3
review foundation + the slice-4 funding foundation + the canonical ledger (bot.py
is not importable in the hermetic sandbox; the route adapters are checked
structurally in test_advertising_slice5_routes.py).

Keeps FOUR concerns strictly separate — review status, funding status,
operational status, delivery execution — and proves the completion boundary:

    approved + funded -> scheduled or active -> paused/resumed -> completed/cancelled

with its guardrails: approval alone cannot activate; funding alone cannot
activate; a suspended advertiser cannot activate or resume; invalid date ranges
are rejected; a scheduled campaign can pause+resume; illegal transitions are
rejected server-side; a non-owner cannot control (404, existence not leaked);
admin pause/cancel/complete are audited; cancellation does NOT silently release
funds; activation causes NO spend or delivery; flag-off routes dark; legacy
tables are never touched.

    python tests/business_os/test_advertising_slice5_api.py   # no pytest needed
"""

import os
import tempfile

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="busos_ad5_"), "test.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB
os.environ["BUSINESS_OS_ADVERTISING"] = "on"

import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from services import db  # noqa: E402
from services.business_os.advertising import service as ad  # noqa: E402
from services.business_os.advertising import funding as adf  # noqa: E402
from services.business_os.advertising import operations as ado  # noqa: E402
from services.business_os.advertising import api as adapi  # noqa: E402
from services.business_os.ledger import ledger  # noqa: E402

ADMIN = 8
ACTIVE = {"account_status": "active", "access_enabled": 1}
SUSPENDED = {"account_status": "suspended", "access_enabled": 1}
_uid_seq = [1900]


def setup_module(module=None):
    ad.ensure_schema()
    ledger.ensure_schema()


def _new_owner():
    _uid_seq[0] += 1
    return _uid_seq[0]


def _assert(cond, msg):
    if not cond:
        raise AssertionError(msg)


def _approve(uid):
    ad.upsert_advertiser(uid)
    ad.set_advertiser_status(uid, "approved", actor=ADMIN)


def _draft(owner, name="Camp", objective="traffic"):
    s, b = adapi.create_draft(
        owner, {"name": name, "objective": objective,
                "destination_url": "https://ex.com"}, context=ACTIVE)
    _assert(s == 201, (s, b))
    return b["campaign"]["campaign_id"]


def _approved_campaign(owner, approve_owner=True):
    if approve_owner:
        _approve(owner)
    cid = _draft(owner)
    s, b = adapi.submit(owner, cid, context=ACTIVE)
    _assert(s == 200, (s, b))
    s, b = adapi.admin_review(ADMIN, cid, "approve")
    _assert(s == 200 and b["after_status"] == "approved", (s, b))
    return cid


def _fund_wallet(uid, cents):
    ledger.post_entry(
        idempotency_key=f"seed:{uid}:{cents}:{os.urandom(4).hex()}",
        actor="test-seed", amount_cents=cents, currency="usd",
        entry_type="seed_deposit", source="platform:ad_funding_source",
        destination=adf._wallet_account(uid))


def _wallet_balance(uid):
    return ledger.get_balance(adf._wallet_account(uid), "usd")


def _escrow_balance(cid):
    return ledger.get_balance(adf._escrow_account(cid), "usd")


def _funded_campaign(owner, budget=5000, wallet=10000, approve_owner=True):
    """approved + wallet-seeded + budget set + funds reserved => activation-ready."""
    cid = _approved_campaign(owner, approve_owner=approve_owner)
    _fund_wallet(owner, wallet)
    s, b = adapi.set_budget(
        owner, cid, {"budget_cents": budget, "currency": "usd"}, context=ACTIVE)
    _assert(s == 200, (s, b))
    s, b = adapi.reserve(
        owner, cid, {"amount_cents": budget, "currency": "usd",
                     "idempotency_key": f"resv-{cid}"}, context=ACTIVE)
    _assert(s == 200 and b["funding"]["funding_status"] == "funded", (s, b))
    return cid


def _audit_rows(cid, action_prefix=None):
    conn = db.connect()
    try:
        rows = [dict(r) for r in conn.execute(
            "SELECT action, actor, before_json, after_json FROM business_os_ad_audit "
            "WHERE campaign_id = ? ORDER BY id", (cid,)).fetchall()]
    finally:
        conn.close()
    if action_prefix:
        rows = [r for r in rows if str(r["action"]).startswith(action_prefix)]
    return rows


# 1 -- flag OFF: every operational handler is dark (404) ---------------------
def test_flag_off_dark():
    os.environ["BUSINESS_OS_ADVERTISING"] = "off"
    for result in (
        adapi.get_operational(1, "nope"),
        adapi.schedule(1, "nope", {}),
        adapi.activate(1, "nope", {}),
        adapi.pause(1, "nope", {}),
        adapi.resume(1, "nope"),
        adapi.cancel(1, "nope", {}),
        adapi.admin_get_operational("nope"),
        adapi.admin_list_operations(),
        adapi.admin_pause(ADMIN, "nope", {}),
        adapi.admin_cancel(ADMIN, "nope", {}),
        adapi.admin_complete(ADMIN, "nope", {}),
    ):
        status, body = result
        _assert(status == 404 and body["ok"] is False, f"expected dark 404, got {result}")
    os.environ["BUSINESS_OS_ADVERTISING"] = "on"


# 2 -- approved + funded can activate; three states shown separately ----------
def test_approved_and_funded_can_activate():
    owner = _new_owner()
    cid = _funded_campaign(owner)
    s, b = adapi.get_operational(owner, cid)
    _assert(s == 200, (s, b))
    op = b["operational"]
    _assert(op["review_status"] == "approved", op)
    _assert(op["funding_status"] == "funded", op)
    _assert(op["activation_ready"] is True, op)
    _assert(op["operational_status"] == "inactive", op)
    _assert(op["delivering"] is False, op)
    # activate directly (immediate activation from inactive)
    s, b = adapi.activate(owner, cid, {})
    _assert(s == 200 and b["operational"]["operational_status"] == "active", b)
    _assert(b["operational"]["activated_at"], "activated_at must be stamped")
    _assert(b["operational"]["delivering"] is False, b)


# 3 -- approved but UNFUNDED cannot activate ---------------------------------
def test_approved_but_unfunded_cannot_activate():
    owner = _new_owner()
    cid = _approved_campaign(owner)  # approved, no budget/reservation
    s, b = adapi.activate(owner, cid, {})
    _assert(s == 409 and b["code"] == "not_funded", b)
    s, b = adapi.schedule(owner, cid, {})
    _assert(s == 409 and b["code"] == "not_funded", b)
    # operational status untouched
    s, b = adapi.get_operational(owner, cid)
    _assert(b["operational"]["operational_status"] == "inactive", b)


# 4 -- funded but NOT review-approved cannot activate ------------------------
def test_funded_but_unapproved_cannot_activate():
    owner = _new_owner()
    cid = _funded_campaign(owner)
    # Simulate the review status regressing while funding remains in place. There
    # is no product path from approved->submitted, so drive it at the data layer
    # to prove the activation gate rejects a funded-but-unapproved campaign.
    conn = db.connect()
    try:
        conn.execute(
            "UPDATE business_os_ad_campaigns SET status = 'submitted' WHERE campaign_id = ?",
            (cid,))
        conn.commit()
    finally:
        conn.close()
    s, b = adapi.activate(owner, cid, {})
    _assert(s == 409 and b["code"] == "not_approved", b)


# 5 -- suspended advertiser cannot activate or resume ------------------------
def test_suspended_advertiser_cannot_activate_or_resume():
    owner = _new_owner()
    cid = _funded_campaign(owner)
    # first bring it to active, then pause so we can test resume too
    _assert(adapi.activate(owner, cid, {})[0] == 200, "precondition activate")
    _assert(adapi.pause(owner, cid, {})[0] == 200, "precondition pause")
    # advertiser approval revoked -> ineligible
    ad.set_advertiser_status(owner, "suspended", actor=ADMIN)
    s, b = adapi.resume(owner, cid, context=ACTIVE)
    _assert(s == 403 and b["code"] == "ineligible", b)
    ad.set_advertiser_status(owner, "approved", actor=ADMIN)  # restore approval
    # account hold (suspended context) overrides advertiser approval -> ineligible
    s, b = adapi.resume(owner, cid, context=SUSPENDED)
    _assert(s == 403 and b["code"] == "ineligible", b)
    # a fresh funded campaign also cannot be first-activated while suspended
    owner2 = _new_owner()
    cid2 = _funded_campaign(owner2)
    s, b = adapi.activate(owner2, cid2, {}, context=SUSPENDED)
    _assert(s == 403 and b["code"] == "ineligible", b)


# 6 -- invalid date ranges + malformed timestamps rejected -------------------
def test_invalid_date_ranges_rejected():
    owner = _new_owner()
    cid = _funded_campaign(owner)
    # end before start
    s, b = adapi.schedule(
        owner, cid,
        {"start_at": "2030-01-02T00:00:00Z", "end_at": "2030-01-01T00:00:00Z"})
    _assert(s == 400 and b["code"] == "bad_window", b)
    # end equal to start (must be strictly after)
    s, b = adapi.schedule(
        owner, cid,
        {"start_at": "2030-01-01T00:00:00Z", "end_at": "2030-01-01T00:00:00Z"})
    _assert(s == 400 and b["code"] == "bad_window", b)
    # unparseable timestamp
    s, b = adapi.schedule(owner, cid, {"start_at": "not-a-date"})
    _assert(s == 400 and b["code"] == "bad_timestamp", b)
    # still inactive — nothing was written
    s, b = adapi.get_operational(owner, cid)
    _assert(b["operational"]["operational_status"] == "inactive", b)
    # a valid window IS accepted and normalized to UTC
    s, b = adapi.schedule(
        owner, cid,
        {"start_at": "2030-01-01T00:00:00+00:00", "end_at": "2030-02-01T12:00:00Z"})
    _assert(s == 200 and b["operational"]["operational_status"] == "scheduled", b)
    _assert(b["operational"]["start_at"].endswith("Z"), b)
    _assert(b["operational"]["end_at"].endswith("Z"), b)


# 7 -- scheduled campaign can pause + resume ---------------------------------
def test_scheduled_can_pause_and_resume():
    owner = _new_owner()
    cid = _funded_campaign(owner)
    _assert(adapi.schedule(owner, cid, {})[0] == 200, "schedule")
    s, b = adapi.pause(owner, cid, {"reason": "hold"})
    _assert(s == 200 and b["operational"]["operational_status"] == "paused", b)
    _assert(b["operational"]["paused_at"], "paused_at stamped")
    s, b = adapi.resume(owner, cid, context=ACTIVE)
    _assert(s == 200 and b["operational"]["operational_status"] == "active", b)
    # active -> completed is admin-only; advertiser cannot self-complete
    # (no advertiser 'complete' verb exists — proven by the api surface)


# 8 -- illegal operational transitions rejected server-side ------------------
def test_illegal_transitions_rejected():
    owner = _new_owner()
    cid = _funded_campaign(owner)
    # pause on inactive is illegal (inactive -> paused not permitted)
    s, b = adapi.pause(owner, cid, {})
    _assert(s == 409 and b["code"] == "illegal_operational_transition", b)
    # cancel a campaign, then every further move is illegal / blocked
    _assert(adapi.activate(owner, cid, {})[0] == 200, "activate")
    _assert(adapi.cancel(owner, cid, {"reason": "done"})[0] == 200, "cancel")
    s, b = adapi.activate(owner, cid, {})
    _assert(s == 409 and b["code"] == "illegal_operational_transition", b)
    s, b = adapi.pause(owner, cid, {})
    _assert(s == 409 and b["code"] == "illegal_operational_transition", b)
    s, b = adapi.resume(owner, cid, context=ACTIVE)
    _assert(s == 409 and b["code"] == "not_paused", b)


# 9 -- non-owner cannot control or read (404, existence not leaked) ----------
def test_non_owner_cannot_control():
    owner = _new_owner()
    cid = _funded_campaign(owner)
    other = _new_owner()
    _approve(other)
    for result in (
        adapi.get_operational(other, cid),
        adapi.schedule(other, cid, {}),
        adapi.activate(other, cid, {}),
        adapi.pause(other, cid, {}),
        adapi.resume(other, cid),
        adapi.cancel(other, cid, {}),
    ):
        s, b = result
        _assert(s == 404 and b["code"] == "not_found", f"non-owner must get 404: {result}")


# 10 -- admin pause / cancel / complete are audited --------------------------
def test_admin_interventions_audited():
    owner = _new_owner()
    cid = _funded_campaign(owner)
    _assert(adapi.activate(owner, cid, {})[0] == 200, "activate")
    # admin pause
    s, b = adapi.admin_pause(ADMIN, cid, {"reason": "policy review"})
    _assert(s == 200 and b["operational"]["operational_status"] == "paused", b)
    # admin resume path is advertiser-only; admin can cancel/complete. Bring back
    # to active via the owner to test admin complete.
    _assert(adapi.resume(owner, cid, context=ACTIVE)[0] == 200, "resume")
    s, b = adapi.admin_complete(ADMIN, cid, {"reason": "run finished"})
    _assert(s == 200 and b["operational"]["operational_status"] == "completed", b)
    # combined admin view exposes review + funding + operational together
    _assert("funding" in b["operational"], b)
    # audit trail recorded each admin transition with actor + before/after state
    admin_audit = _audit_rows(cid, action_prefix="campaign_op_admin_")
    actions = {r["action"] for r in admin_audit}
    _assert("campaign_op_admin_pause" in actions, actions)
    _assert("campaign_op_admin_complete" in actions, actions)
    for r in admin_audit:
        _assert(str(ADMIN) in str(r["actor"]) or r["actor"] == ADMIN, r)
        _assert("operational_status" in (r["before_json"] or ""), r)
        _assert("operational_status" in (r["after_json"] or ""), r)


# 11 -- cancellation does NOT silently release funds -------------------------
def test_cancel_does_not_release_funds():
    owner = _new_owner()
    cid = _funded_campaign(owner)
    _assert(_escrow_balance(cid) == 5000, _escrow_balance(cid))
    _assert(adapi.activate(owner, cid, {})[0] == 200, "activate")
    s, b = adapi.cancel(owner, cid, {"reason": "advertiser stopped"})
    _assert(s == 200 and b["operational"]["operational_status"] == "cancelled", b)
    # funds are STILL escrowed — cancel never touched the ledger
    _assert(_escrow_balance(cid) == 5000, _escrow_balance(cid))
    # funding_status is unchanged (still funded); releasing is a separate call
    s, b = adapi.get_funding(owner, cid)
    _assert(b["funding"]["funding_status"] == "funded", b)
    # admin cancel likewise releases nothing
    owner2 = _new_owner()
    cid2 = _funded_campaign(owner2)
    _assert(adapi.activate(owner2, cid2, {})[0] == 200, "activate2")
    _assert(adapi.admin_cancel(ADMIN, cid2, {"reason": "policy"})[0] == 200, "admin cancel")
    _assert(_escrow_balance(cid2) == 5000, _escrow_balance(cid2))


# 12 -- activation causes NO spend and NO delivery ---------------------------
def test_activation_no_spend_no_delivery():
    owner = _new_owner()
    cid = _funded_campaign(owner)
    wallet_before = _wallet_balance(owner)
    escrow_before = _escrow_balance(cid)
    _assert(adapi.activate(owner, cid, {})[0] == 200, "activate")
    # no money moved by activation
    _assert(_wallet_balance(owner) == wallet_before, _wallet_balance(owner))
    _assert(_escrow_balance(cid) == escrow_before, _escrow_balance(cid))
    # operational view exposes no delivery/impression/spend fields and never claims
    # to be delivering
    s, b = adapi.get_operational(owner, cid)
    op = b["operational"]
    _assert(op["delivering"] is False, op)
    for banned in ("impressions", "clicks", "spend", "auction", "served",
                   "views", "delivered", "audience", "placement", "pacing"):
        _assert(banned not in op, f"operational view leaked delivery field {banned!r}")
    # legacy delivery table is never created by the canonical operational path
    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='pulse_ad_campaigns'"
        ).fetchone()
    finally:
        conn.close()
    _assert(row is None, "operational path must not create legacy pulse_ad_campaigns")


# 13 -- admin listing + combined read (cross-owner) --------------------------
def test_admin_listing_and_combined_read():
    owner = _new_owner()
    cid = _funded_campaign(owner)
    _assert(adapi.activate(owner, cid, {})[0] == 200, "activate")
    s, b = adapi.admin_get_operational(cid)
    _assert(s == 200, (s, b))
    _assert(b["operational"]["operational_status"] == "active", b)
    _assert(b["operational"]["review_status"] == "approved", b)
    _assert(b["operational"]["funding_status"] == "funded", b)
    # cross-owner listing with a status filter
    s, b = adapi.admin_list_operations(operational_status="active")
    _assert(s == 200 and any(r["campaign_id"] == cid for r in b["operations"]), b)
    # unknown status filter rejected
    s, b = adapi.admin_list_operations(operational_status="bogus")
    _assert(s == 400 and b["code"] == "bad_status", b)


# 14 -- unknown fields on schedule/activate rejected -------------------------
def test_unknown_fields_rejected():
    owner = _new_owner()
    cid = _funded_campaign(owner)
    s, b = adapi.schedule(owner, cid, {"operational_status": "active"})
    _assert(s == 400 and b["code"] == "unknown_field", b)
    s, b = adapi.activate(owner, cid, {"budget_cents": 999})
    _assert(s == 400 and b["code"] == "unknown_field", b)


# 15 -- full happy path: scheduled -> active -> paused -> active -> completed -
def test_full_lifecycle_happy_path():
    owner = _new_owner()
    cid = _funded_campaign(owner)
    _assert(adapi.schedule(owner, cid, {})[1]["operational"]["operational_status"] == "scheduled", "scheduled")
    _assert(adapi.activate(owner, cid, {})[1]["operational"]["operational_status"] == "active", "active")
    _assert(adapi.pause(owner, cid, {})[1]["operational"]["operational_status"] == "paused", "paused")
    _assert(adapi.resume(owner, cid, context=ACTIVE)[1]["operational"]["operational_status"] == "active", "resumed")
    _assert(adapi.admin_complete(ADMIN, cid, {})[1]["operational"]["operational_status"] == "completed", "completed")
    # terminal: no further advertiser transition succeeds
    s, b = adapi.pause(owner, cid, {})
    _assert(s == 409 and b["code"] == "illegal_operational_transition", b)
    # review + funding states are STILL exactly what they were — untouched
    s, b = adapi.get_own_campaign(owner, cid)
    _assert(b["campaign"]["status"] == "approved", b)
    _assert(_escrow_balance(cid) == 5000, "operational lifecycle never moved money")


# --- standalone runner ------------------------------------------------------
def _run_standalone():
    setup_module()
    tests = [
        test_flag_off_dark,
        test_approved_and_funded_can_activate,
        test_approved_but_unfunded_cannot_activate,
        test_funded_but_unapproved_cannot_activate,
        test_suspended_advertiser_cannot_activate_or_resume,
        test_invalid_date_ranges_rejected,
        test_scheduled_can_pause_and_resume,
        test_illegal_transitions_rejected,
        test_non_owner_cannot_control,
        test_admin_interventions_audited,
        test_cancel_does_not_release_funds,
        test_activation_no_spend_no_delivery,
        test_admin_listing_and_combined_read,
        test_unknown_fields_rejected,
        test_full_lifecycle_happy_path,
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
