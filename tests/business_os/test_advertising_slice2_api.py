"""Advertising slice 2 — controller (HTTP-logic) test matrix.

Exercises services/business_os/advertising/api.py directly. bot.py is not
importable in the hermetic sandbox (stripe/flask/telegram + no PyPI), so the
route *adapters* are verified structurally in test_advertising_slice2_routes.py;
here we prove the actual decision logic every route delegates to: flag-off
darkness, eligibility precedence, ownership 404, allowlisting (incl. "clients
cannot set status directly"), validation, the draft lifecycle, and the admin
approval/suspension transitions with before/after for the audit trail.

    python tests/business_os/test_advertising_slice2_api.py   # no pytest needed
"""

import os
import tempfile

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="busos_ad2_"), "test.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB
os.environ["BUSINESS_OS_ADVERTISING"] = "on"

import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from services import db  # noqa: E402
from services.business_os.advertising import service as ad  # noqa: E402
from services.business_os.advertising import api as adapi  # noqa: E402

OWNER = 600
OWNER2 = 601
ADMIN = 7
PENDING_U = 602
HELD = 603
ACTIVE = {"account_status": "active", "access_enabled": 1}
SUSPENDED = {"account_status": "suspended", "access_enabled": 1}


def setup_module(module=None):
    ad.ensure_schema()


def _approve(uid):
    ad.upsert_advertiser(uid)
    ad.set_advertiser_status(uid, "approved", actor=ADMIN)


def _assert(cond, msg):
    if not cond:
        raise AssertionError(msg)


# 1 -- flag OFF: every handler is dark (404) --------------------------------
def test_flag_off_dark():
    os.environ["BUSINESS_OS_ADVERTISING"] = "off"
    for result in (
        adapi.get_eligibility(OWNER, context=ACTIVE),
        adapi.register_advertiser(OWNER, {}),
        adapi.create_draft(OWNER, {"name": "x", "objective": "traffic"}, context=ACTIVE),
        adapi.list_own_campaigns(OWNER),
        adapi.get_own_campaign(OWNER, "nope"),
        adapi.update_draft(OWNER, "nope", {"name": "y"}),
        adapi.lifecycle(OWNER, "nope", "archive"),
        adapi.admin_list_advertisers(),
        adapi.admin_get_advertiser(OWNER),
        adapi.admin_set_advertiser_status(ADMIN, OWNER, "approved"),
        adapi.admin_list_campaigns(),
        adapi.admin_get_campaign("nope"),
    ):
        status, body = result
        _assert(status == 404 and body["ok"] is False, f"expected dark 404, got {result}")
    os.environ["BUSINESS_OS_ADVERTISING"] = "on"


# 2 -- eligibility view: explainable, un-merged policy fields ----------------
def test_eligibility_view_fields():
    # unregistered
    s, b = adapi.get_eligibility(9999, context=ACTIVE)
    _assert(s == 200, s)
    e = b["eligibility"]
    _assert(e["eligible"] is False and e["rollout_enabled"] is True, e)
    _assert(e["account_hold"] is False, e)
    _assert(e["denial_reason"] == "advertiser_not_registered", e)
    # pending
    ad.upsert_advertiser(PENDING_U)
    _, b = adapi.get_eligibility(PENDING_U, context=ACTIVE)
    _assert(b["eligibility"]["denial_reason"] == "advertiser_pending", b)
    _assert(b["eligibility"]["advertiser_status"] == "pending", b)
    # approved -> eligible, no denial reason
    _approve(OWNER)
    _, b = adapi.get_eligibility(OWNER, context=ACTIVE)
    _assert(b["eligibility"]["eligible"] is True, b)
    _assert(b["eligibility"]["denial_reason"] is None, b)
    # suspended account overrides an approved advertiser
    _approve(HELD)
    _, b = adapi.get_eligibility(HELD, context=SUSPENDED)
    e = b["eligibility"]
    _assert(e["eligible"] is False and e["account_hold"] is True, e)
    _assert(e["denial_reason"] == "account_suspended", e)


# 3 -- register: allowlist + pending ----------------------------------------
def test_register():
    s, b = adapi.register_advertiser(OWNER2, {"display_name": "Beta", "notes": "hi"})
    _assert(s == 200 and b["advertiser"]["status"] == "pending", b)
    # unknown field rejected
    s, b = adapi.register_advertiser(OWNER2, {"evil": 1})
    _assert(s == 400 and b["code"] == "unknown_field", b)


# 4 -- create draft: eligibility + validation + status-immutability ----------
def test_create_draft():
    _approve(OWNER)
    # ineligible (unregistered) -> 403
    s, b = adapi.create_draft(7777, {"name": "x", "objective": "traffic"}, context=ACTIVE)
    _assert(s == 403 and b["code"] == "ineligible", b)
    # approved but suspended account -> 403 (hold precedence)
    _approve(HELD)
    s, b = adapi.create_draft(HELD, {"name": "x", "objective": "traffic"}, context=SUSPENDED)
    _assert(s == 403 and b["code"] == "ineligible", b)
    # client attempting to set status directly -> unknown_field
    s, b = adapi.create_draft(OWNER, {"name": "x", "objective": "traffic", "status": "archived"}, context=ACTIVE)
    _assert(s == 400 and b["code"] == "unknown_field", b)
    # bad objective
    s, b = adapi.create_draft(OWNER, {"name": "x", "objective": "nope"}, context=ACTIVE)
    _assert(s == 400 and b["code"] == "bad_objective", b)
    # success -> 201, status forced to draft
    s, b = adapi.create_draft(OWNER, {"name": "Launch", "objective": "Traffic",
                                      "destination_url": "https://ex.com"}, context=ACTIVE)
    _assert(s == 201 and b["campaign"]["status"] == "draft", b)
    _assert(b["campaign"]["objective"] == "traffic", b)


# 5 -- ownership: read own vs other ------------------------------------------
def test_ownership_read():
    _approve(OWNER)
    _approve(OWNER2)
    _, b = adapi.create_draft(OWNER, {"name": "Owned", "objective": "leads"}, context=ACTIVE)
    cid = b["campaign"]["campaign_id"]
    s, _ = adapi.get_own_campaign(OWNER, cid)
    _assert(s == 200, s)
    # other advertiser -> 404 (existence not leaked)
    s, b = adapi.get_own_campaign(OWNER2, cid)
    _assert(s == 404 and b["code"] == "not_found", b)


# 6 -- update draft: allowlist, no-fields, status-immutable, not-editable ----
def test_update_draft():
    _approve(OWNER)
    _, b = adapi.create_draft(OWNER, {"name": "U", "objective": "leads"}, context=ACTIVE)
    cid = b["campaign"]["campaign_id"]
    # cannot set status
    s, b2 = adapi.update_draft(OWNER, cid, {"status": "archived"})
    _assert(s == 400 and b2["code"] == "unknown_field", b2)
    # no fields
    s, b2 = adapi.update_draft(OWNER, cid, {})
    _assert(s == 400 and b2["code"] == "no_fields", b2)
    # valid update
    s, b2 = adapi.update_draft(OWNER, cid, {"name": "Renamed", "objective": "awareness"})
    _assert(s == 200 and b2["campaign"]["name"] == "Renamed", b2)
    _assert(b2["campaign"]["objective"] == "awareness", b2)
    # non-owner update -> 404
    s, b2 = adapi.update_draft(OWNER2, cid, {"name": "hax"})
    _assert(s == 404, b2)
    # archived campaign not editable
    adapi.lifecycle(OWNER, cid, "archive")
    s, b2 = adapi.update_draft(OWNER, cid, {"name": "late"})
    _assert(s == 409 and b2["code"] == "not_editable", b2)


# 7 -- lifecycle: archive/restore only, bad action, ownership ----------------
def test_lifecycle():
    _approve(OWNER)
    _, b = adapi.create_draft(OWNER, {"name": "LC", "objective": "traffic"}, context=ACTIVE)
    cid = b["campaign"]["campaign_id"]
    s, b2 = adapi.lifecycle(OWNER, cid, "archive")
    _assert(s == 200 and b2["campaign"]["status"] == "archived", b2)
    s, b2 = adapi.lifecycle(OWNER, cid, "restore")
    _assert(s == 200 and b2["campaign"]["status"] == "draft", b2)
    # unknown verb
    s, b2 = adapi.lifecycle(OWNER, cid, "publish")
    _assert(s == 400 and b2["code"] == "bad_action", b2)
    # non-owner
    s, b2 = adapi.lifecycle(OWNER2, cid, "archive")
    _assert(s == 404, b2)


# 8 -- admin: status transitions carry before/after + list/get ---------------
def test_admin():
    ad.upsert_advertiser(OWNER)  # ensure exists (pending)
    ad.set_advertiser_status(OWNER, "pending", actor=ADMIN) if False else None
    # approve
    s, b = adapi.admin_set_advertiser_status(ADMIN, OWNER, "approved", reason="ok")
    _assert(s == 200 and b["after_status"] == "approved", b)
    _assert("before_status" in b, b)
    # suspend
    s, b = adapi.admin_set_advertiser_status(ADMIN, OWNER, "suspended", reason="tos")
    _assert(s == 200 and b["before_status"] == "approved" and b["after_status"] == "suspended", b)
    # bad status
    s, b = adapi.admin_set_advertiser_status(ADMIN, OWNER, "banana")
    _assert(s == 400, b)
    # missing advertiser get -> 404
    s, b = adapi.admin_get_advertiser(88888)
    _assert(s == 404, b)
    # list advertisers + campaigns
    s, b = adapi.admin_list_advertisers()
    _assert(s == 200 and isinstance(b["advertisers"], list), b)
    s, b = adapi.admin_list_campaigns()
    _assert(s == 200 and isinstance(b["campaigns"], list), b)


# --- standalone runner ------------------------------------------------------
def _run_standalone():
    setup_module()
    tests = [
        test_flag_off_dark,
        test_eligibility_view_fields,
        test_register,
        test_create_draft,
        test_ownership_read,
        test_update_draft,
        test_lifecycle,
        test_admin,
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
