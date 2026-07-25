"""Advertising vertical — slice-1 test matrix (flag-gated draft campaigns).

Proves the smallest end-to-end advertiser capability directly against the
canonical service (bot.py is not importable in this hermetic sandbox):

  * feature-flag gating: with BUSINESS_OS_ADVERTISING off, every write/eligibility
    entrypoint raises and nothing is created (fully reversible / inert);
  * advertiser approval state (register -> pending -> approved) as a SEPARATE input;
  * eligibility composes account-hold + approval + rollout flag without merging;
  * account hold overrides an approved advertiser (shared suspension authority);
  * server-side validation (name required/too long, objective, destination URL);
  * draft creation forces status='draft' and records ownership;
  * ownership enforcement on read + transition (non-owner sees 404);
  * lifecycle draft<->archived only, illegal transitions rejected, idempotent no-op;
  * admin cross-owner visibility;
  * owner isolation (one advertiser cannot see/act on another's campaign);
  * audit rows written for create/transition/status.

    python -m pytest tests/business_os/test_advertising_slice1.py
    python tests/business_os/test_advertising_slice1.py   # no pytest needed
"""

import os
import tempfile

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="busos_ad_"), "test.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB
os.environ["BUSINESS_OS_ADVERTISING"] = "on"

import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from services import db  # noqa: E402
from services.business_os.advertising import service as ad  # noqa: E402

OWNER = 500
OWNER2 = 501
ADMIN = 9
HELD = 502


def setup_module(module=None):
    ad.ensure_schema()
    conn = db.connect()
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS users ("
            "user_id INTEGER PRIMARY KEY, account_status TEXT DEFAULT 'active', "
            "access_enabled INTEGER DEFAULT 1)"
        )
        conn.commit()
    finally:
        conn.close()


def _approve(uid):
    ad.upsert_advertiser(uid)
    ad.set_advertiser_status(uid, "approved", actor=ADMIN)


def _ctx(status="active", access=1):
    return {"account_status": status, "access_enabled": access}


# 1 -- flag OFF: entrypoints raise, nothing created --------------------------
def test_flag_off_is_inert():
    os.environ["BUSINESS_OS_ADVERTISING"] = "off"
    assert ad.is_enabled() is False
    for call in (
        lambda: ad.upsert_advertiser(OWNER),
        lambda: ad.create_campaign_draft(OWNER, name="x", objective="traffic"),
        lambda: ad.admin_list_campaigns(),
    ):
        try:
            call()
            assert False, "expected AdvertisingError when flag off"
        except ad.AdvertisingError as e:
            assert e.http_status == 503 and e.code == "disabled"
    os.environ["BUSINESS_OS_ADVERTISING"] = "on"


# 2 -- register creates a pending advertiser ---------------------------------
def test_register_pending():
    a = ad.upsert_advertiser(OWNER, display_name="Acme")
    assert a["status"] == "pending"
    assert a["display_name"] == "Acme"
    # idempotent: second call keeps pending, refreshes notes
    a2 = ad.upsert_advertiser(OWNER, notes="hi")
    assert a2["status"] == "pending" and a2["notes"] == "hi"


# 3 -- eligibility gating: not registered / pending / disabled flag ----------
def test_eligibility_negative_paths():
    # unregistered user
    e = ad.advertiser_eligibility(9999, context=_ctx())
    assert e["eligible"] is False and e["reason"] == "advertiser_not_registered"
    # pending (OWNER registered in test 2 but not approved yet in isolation runs)
    ad.upsert_advertiser(OWNER)
    e = ad.advertiser_eligibility(OWNER, context=_ctx())
    assert e["eligible"] is False and e["reason"] == "advertiser_pending"
    # flag disabled short-circuits before anything else
    os.environ["BUSINESS_OS_ADVERTISING"] = "off"
    e = ad.advertiser_eligibility(OWNER, context=_ctx())
    assert e["eligible"] is False and e["reason"] == "advertising_disabled"
    os.environ["BUSINESS_OS_ADVERTISING"] = "on"


# 4 -- approval makes an advertiser eligible ---------------------------------
def test_approval_makes_eligible():
    _approve(OWNER)
    e = ad.advertiser_eligibility(OWNER, context=_ctx())
    assert e["eligible"] is True and e["reason"] == "ok"
    assert e["advertiser_status"] == "approved"


# 5 -- account hold overrides an approved advertiser -------------------------
def test_account_hold_overrides_approval():
    _approve(HELD)
    # approved, but suspended account -> not eligible, hold reason surfaced
    e = ad.advertiser_eligibility(HELD, context=_ctx("suspended", 1))
    assert e["eligible"] is False
    assert e["reason"] == "account_suspended"
    assert e["advertiser_status"] is None or e["account_hold"]["on_hold"] is True
    # access_enabled=0 is also a hold
    e2 = ad.advertiser_eligibility(HELD, context=_ctx("active", 0))
    assert e2["eligible"] is False and e2["reason"] == "account_access_disabled"


# 6 -- create draft: validation ----------------------------------------------
def test_create_validation():
    _approve(OWNER)
    bad = [
        (dict(name="", objective="traffic"), "name_required"),
        (dict(name="x" * 121, objective="traffic"), "name_too_long"),
        (dict(name="ok", objective="nope"), "bad_objective"),
        (dict(name="ok", objective="traffic", destination_url="ftp://x"), "bad_url"),
        (dict(name="ok", objective="traffic", destination_url="h" * 2049), "url_too_long"),
    ]
    for kwargs, code in bad:
        try:
            ad.create_campaign_draft(OWNER, context=_ctx(), **kwargs)
            assert False, f"expected {code}"
        except ad.AdvertisingError as e:
            assert e.code == code, (e.code, code)


# 7 -- create draft: success forces status='draft' + ownership ---------------
def test_create_success():
    _approve(OWNER)
    c = ad.create_campaign_draft(
        OWNER, name="Summer Launch", objective="Traffic",
        destination_url="https://example.com/lp", context=_ctx())
    assert c["status"] == "draft"
    assert c["advertiser_user_id"] == str(OWNER)
    assert c["objective"] == "traffic"  # normalized
    assert c["name"] == "Summer Launch"
    # ineligible user cannot create
    try:
        ad.create_campaign_draft(7777, name="x", objective="traffic", context=_ctx())
        assert False
    except ad.AdvertisingError as e:
        assert e.http_status == 403 and e.code == "ineligible"


# 8 -- ownership enforcement on read -----------------------------------------
def test_ownership_read():
    _approve(OWNER)
    c = ad.create_campaign_draft(OWNER, name="Owned", objective="leads", context=_ctx())
    cid = c["campaign_id"]
    # owner reads fine
    assert ad.get_campaign(cid, requester_user_id=OWNER)["campaign_id"] == cid
    # non-owner gets 404 (existence not leaked)
    try:
        ad.get_campaign(cid, requester_user_id=OWNER2)
        assert False
    except ad.AdvertisingError as e:
        assert e.http_status == 404
    # admin (requester None) can read
    assert ad.admin_get_campaign(cid)["campaign_id"] == cid


# 9 -- lifecycle: draft<->archived, illegal rejected, idempotent no-op -------
def test_lifecycle():
    _approve(OWNER)
    c = ad.create_campaign_draft(OWNER, name="LC", objective="awareness", context=_ctx())
    cid = c["campaign_id"]
    # draft -> archived
    assert ad.transition_campaign(cid, "archived", requester_user_id=OWNER)["status"] == "archived"
    # archived -> draft
    assert ad.transition_campaign(cid, "draft", requester_user_id=OWNER)["status"] == "draft"
    # idempotent no-op (draft -> draft)
    assert ad.transition_campaign(cid, "draft", requester_user_id=OWNER)["status"] == "draft"
    # unknown status
    try:
        ad.transition_campaign(cid, "published", requester_user_id=OWNER)
        assert False
    except ad.AdvertisingError as e:
        assert e.code == "bad_status"
    # non-owner cannot transition
    try:
        ad.transition_campaign(cid, "archived", requester_user_id=OWNER2)
        assert False
    except ad.AdvertisingError as e:
        assert e.http_status == 404


# 10 -- owner isolation + admin visibility -----------------------------------
def test_isolation_and_admin_visibility():
    _approve(OWNER)
    _approve(OWNER2)
    a = ad.create_campaign_draft(OWNER, name="A", objective="traffic", context=_ctx())
    b = ad.create_campaign_draft(OWNER2, name="B", objective="traffic", context=_ctx())
    owner_ids = {c["campaign_id"] for c in ad.list_campaigns_for_owner(OWNER)}
    owner2_ids = {c["campaign_id"] for c in ad.list_campaigns_for_owner(OWNER2)}
    assert a["campaign_id"] in owner_ids and a["campaign_id"] not in owner2_ids
    assert b["campaign_id"] in owner2_ids and b["campaign_id"] not in owner_ids
    # admin sees across owners
    all_ids = {c["campaign_id"] for c in ad.admin_list_campaigns()}
    assert {a["campaign_id"], b["campaign_id"]}.issubset(all_ids)
    # admin advertiser listing
    adv_ids = {r["user_id"] for r in ad.admin_list_advertisers(status="approved")}
    assert str(OWNER) in adv_ids and str(OWNER2) in adv_ids


# 11 -- audit rows written ----------------------------------------------------
def test_audit_written():
    _approve(OWNER)
    c = ad.create_campaign_draft(OWNER, name="Audited", objective="traffic", context=_ctx())
    cid = c["campaign_id"]
    ad.transition_campaign(cid, "archived", requester_user_id=OWNER)
    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT action FROM business_os_ad_audit WHERE campaign_id = ? "
            "ORDER BY id", (cid,)
        ).fetchall()
    finally:
        conn.close()
    actions = [r["action"] for r in rows]
    assert "campaign_create" in actions and "campaign_transition" in actions


# --- standalone runner ------------------------------------------------------
def _run_standalone():
    setup_module()
    tests = [
        test_flag_off_is_inert,
        test_register_pending,
        test_eligibility_negative_paths,
        test_approval_makes_eligible,
        test_account_hold_overrides_approval,
        test_create_validation,
        test_create_success,
        test_ownership_read,
        test_lifecycle,
        test_isolation_and_admin_visibility,
        test_audit_written,
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
