"""Advertising slice 7 — delivery controller matrix (services/business_os/advertising/api.py).

The service layer (delivery/eligibility/selection/frequency/events) is exercised
end-to-end in test_advertising_slice7_delivery.py. THIS suite pins the thin
controller contract that bot.py's route adapters depend on: every handler returns
a ``(int status, dict body)`` tuple with ``body["ok"]`` bool; the whole surface is
DARK (404) when the flag is off; unknown request fields are rejected with a curated
400 ``unknown_field`` (privacy allowlist); an impression with no token is a 400
``missing_token``; and the admin handlers are strictly read-only shapes.

bot.py itself is not importable in the hermetic sandbox; its route wiring is checked
structurally in test_advertising_slice7_routes.py. Here we call the controller the
same way bot.py does: pass the SESSION viewer id + the request payload dict.

    python tests/business_os/test_advertising_slice7_api.py   # no pytest needed
"""

import os
import tempfile

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="busos_ad7api_"), "test.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB
os.environ["BUSINESS_OS_ADVERTISING"] = "on"

import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from services import db  # noqa: E402
from services.business_os.advertising import service as ad  # noqa: E402
from services.business_os.advertising import service as _svc  # noqa: E402
from services.business_os.advertising import funding as adf  # noqa: E402
from services.business_os.advertising import api as adapi  # noqa: E402
from services.business_os.advertising import delivery as deliv  # noqa: E402
from services.business_os.ledger import ledger  # noqa: E402

ADMIN = 8
ACTIVE = {"account_status": "active", "access_enabled": 1}
_uid_seq = [6100]
_media_seq = [11000]
_DEST_USER = 555


def setup_module(module=None):
    ad.ensure_schema()
    ledger.ensure_schema()
    conn = db.connect()
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS pulse_media_assets "
            "(id INTEGER PRIMARY KEY, owner_user_id TEXT, media_type TEXT, "
            "processing_status TEXT)")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS users "
            "(id INTEGER PRIMARY KEY, display_name TEXT, username TEXT)")
        conn.execute("CREATE TABLE IF NOT EXISTS pulse_posts (id INTEGER PRIMARY KEY)")
        conn.execute("CREATE TABLE IF NOT EXISTS pulse_reels (id INTEGER PRIMARY KEY)")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS marketplace_listings (id INTEGER PRIMARY KEY)")
        conn.execute(
            "INSERT OR IGNORE INTO users (id, display_name, username) VALUES (?, ?, ?)",
            (_DEST_USER, "Dest Profile", "dest"))
        conn.commit()
    finally:
        conn.close()


def _assert(cond, msg):
    if not cond:
        raise AssertionError(msg)


def _new_owner():
    _uid_seq[0] += 1
    uid = _uid_seq[0]
    conn = db.connect()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO users (id, display_name, username) VALUES (?, ?, ?)",
            (uid, f"Advertiser {uid}", f"adv{uid}"))
        conn.commit()
    finally:
        conn.close()
    return uid


def _seed_media(owner, media_type="image", status="ready"):
    _media_seq[0] += 1
    mid = _media_seq[0]
    conn = db.connect()
    try:
        conn.execute(
            "INSERT INTO pulse_media_assets (id, owner_user_id, media_type, "
            "processing_status) VALUES (?, ?, ?, ?)",
            (mid, str(_svc._sid(owner)), media_type, status))
        conn.commit()
    finally:
        conn.close()
    return mid


def _approve(uid):
    ad.upsert_advertiser(uid)
    ad.set_advertiser_status(uid, "approved", actor=ADMIN)


def _draft(owner):
    s, b = adapi.create_draft(
        owner, {"name": "Camp", "objective": "traffic",
                "destination_url": "https://ex.com"}, context=ACTIVE)
    _assert(s == 201, (s, b))
    return b["campaign"]["campaign_id"]


def _active_campaign(owner):
    _approve(owner)
    cid = _draft(owner)
    s, b = adapi.submit(owner, cid, context=ACTIVE)
    _assert(s == 200, (s, b))
    s, b = adapi.admin_review(ADMIN, cid, "approve")
    _assert(s == 200, (s, b))
    ledger.post_entry(
        idempotency_key=f"seed:{owner}:{os.urandom(4).hex()}",
        actor="test-seed", amount_cents=10000, currency="usd",
        entry_type="seed_deposit", source="platform:ad_funding_source",
        destination=adf._wallet_account(owner))
    s, b = adapi.set_budget(
        owner, cid, {"budget_cents": 5000, "currency": "usd"}, context=ACTIVE)
    _assert(s == 200, (s, b))
    s, b = adapi.reserve(
        owner, cid, {"amount_cents": 5000, "currency": "usd",
                     "idempotency_key": f"resv-{cid}"}, context=ACTIVE)
    _assert(s == 200, (s, b))
    s, b = adapi.activate(owner, cid, {})
    _assert(s == 200, (s, b))
    return cid


def _approved_ad_set(owner, cid, placements=("feed", "reels")):
    s, b = adapi.create_ad_set(
        owner, cid, {"name": "AS", "placements": list(placements),
                     "audience": {"countries": ["us"]}}, context=ACTIVE)
    _assert(s == 201, (s, b))
    asid = b["ad_set"]["ad_set_id"]
    s, b = adapi.ad_set_lifecycle(owner, asid, "submit")
    _assert(s == 200, (s, b))
    s, b = adapi.admin_review_ad_set(ADMIN, asid, "approve")
    _assert(s == 200, (s, b))
    return asid


def _approved_creative(owner, asid, creative_type="image"):
    media_type = "video" if creative_type in ("video", "reels_video") else "image"
    mid = _seed_media(owner, media_type=media_type)
    s, b = adapi.create_creative(
        owner, asid, {"creative_type": creative_type, "media_asset_id": mid,
                      "headline": "Buy now", "destination_type": "profile",
                      "destination_ref": _DEST_USER}, context=ACTIVE)
    _assert(s == 201, (s, b))
    crid = b["creative"]["creative_id"]
    s, b = adapi.creative_lifecycle(owner, crid, "submit")
    _assert(s == 200, (s, b))
    s, b = adapi.admin_review_creative(ADMIN, crid, "approve")
    _assert(s == 200, (s, b))
    return crid


def _ready_feed(owner=None):
    owner = owner or _new_owner()
    cid = _active_campaign(owner)
    asid = _approved_ad_set(owner, cid, placements=("feed", "reels"))
    crid = _approved_creative(owner, asid, creative_type="image")
    return {"owner": owner, "cid": cid, "asid": asid, "crid": crid}


# 1 -- every handler is DARK (404) when the flag is off --------------------
def test_all_handlers_dark_when_off():
    os.environ["BUSINESS_OS_ADVERTISING"] = "off"
    try:
        calls = [
            lambda: adapi.request_delivery(6001, "feed", {}),
            lambda: adapi.record_impression(6001, "adlv_x", {"token": "t"}),
            lambda: adapi.record_click(6001, "adlv_x", {}),
            lambda: adapi.admin_list_deliveries(),
            lambda: adapi.admin_get_delivery("adlv_x"),
            lambda: adapi.admin_list_impressions(),
            lambda: adapi.admin_list_clicks(),
        ]
        for call in calls:
            s, b = call()
            _assert(s == 404, f"flag-off must be 404 dark, got {s}: {b}")
            _assert(b.get("ok") is False, f"dark body must be ok:false: {b}")
    finally:
        os.environ["BUSINESS_OS_ADVERTISING"] = "on"


# 2 -- request_delivery happy path returns a sponsored payload -------------
def test_request_delivery_ok():
    _ready_feed()
    s, b = adapi.request_delivery(6002, "feed", {"country": "us"})
    _assert(s == 200 and b["ok"] is True, (s, b))
    _assert(b["placement"] == "feed", b)
    sp = b.get("sponsored")
    _assert(sp is not None and sp["sponsored"] is True, b)
    _assert(sp["impression_token"], sp)
    # controller never leaks the raw hierarchy into the client body
    for banned in ("advertiser_user_id", "campaign_id", "subject_ref"):
        _assert(banned not in sp, f"leaked {banned}")


# 3 -- request_delivery rejects unknown request fields (privacy allowlist) --
def test_request_delivery_unknown_field_rejected():
    _ready_feed()
    s, b = adapi.request_delivery(
        6003, "feed", {"country": "us", "ssn": "123-45-6789"})
    _assert(s == 400 and b["ok"] is False, (s, b))
    _assert(b.get("code") == "unknown_field", b)


# 4 -- request_delivery: unsupported placement -> curated 400 --------------
def test_request_delivery_bad_placement():
    s, b = adapi.request_delivery(6004, "stories", {})
    _assert(s == 400 and b["ok"] is False, (s, b))
    _assert(b.get("code") == "bad_placement", b)


# 5 -- impression: token required, then happy + idempotent -----------------
def test_impression_requires_token_then_ok():
    _ready_feed()
    s, b = adapi.request_delivery(6005, "feed", {"country": "us"})
    sp = b["sponsored"]
    did, tok = sp["delivery_id"], sp["impression_token"]
    # no token -> 400 missing_token
    s, b = adapi.record_impression(6005, did, {"placement": "feed"})
    _assert(s == 400 and b.get("code") == "missing_token", (s, b))
    # with token -> 200, not a duplicate
    s, b = adapi.record_impression(6005, did, {"token": tok, "placement": "feed"})
    _assert(s == 200 and b["ok"] is True, (s, b))
    _assert(b["impression"]["duplicate"] is False, b)
    _assert(b["impression"]["billing_processed"] is False, b)
    # idempotent replay -> duplicate True, same event id
    s2, b2 = adapi.record_impression(6005, did, {"token": tok, "placement": "feed"})
    _assert(s2 == 200 and b2["impression"]["duplicate"] is True, (s2, b2))
    _assert(b2["impression"]["event_id"] == b["impression"]["event_id"], (b, b2))


# 6 -- impression: unknown field rejected ----------------------------------
def test_impression_unknown_field_rejected():
    _ready_feed()
    s, b = adapi.request_delivery(6006, "feed", {"country": "us"})
    sp = b["sponsored"]
    s, b = adapi.record_impression(
        6006, sp["delivery_id"], {"token": sp["impression_token"], "creative_id": "x"})
    _assert(s == 400 and b.get("code") == "unknown_field", (s, b))


# 7 -- impression: bad token surfaces curated 403 --------------------------
def test_impression_bad_token():
    _ready_feed()
    s, b = adapi.request_delivery(6007, "feed", {"country": "us"})
    did = b["sponsored"]["delivery_id"]
    s, b = adapi.record_impression(6007, did, {"token": "wrong", "placement": "feed"})
    _assert(s == 403 and b.get("code") == "bad_token", (s, b))


# 8 -- click: requires impression, then ok + server destination ------------
def test_click_flow():
    _ready_feed()
    s, b = adapi.request_delivery(6008, "feed", {"country": "us"})
    sp = b["sponsored"]
    did, tok = sp["delivery_id"], sp["impression_token"]
    # click before impression -> 409 impression_required
    s, b = adapi.record_click(6008, did, {})
    _assert(s == 409 and b.get("code") == "impression_required", (s, b))
    adapi.record_impression(6008, did, {"token": tok, "placement": "feed"})
    s, b = adapi.record_click(6008, did, {})
    _assert(s == 200 and b["ok"] is True, (s, b))
    _assert(b["click"]["destination"]["type"] == "profile", b)
    _assert(b["click"]["destination"]["ref"] == str(_DEST_USER), b)
    # idempotent replay
    s2, b2 = adapi.record_click(6008, did, {})
    _assert(s2 == 200 and b2["click"]["duplicate"] is True, (s2, b2))


# 9 -- click: unknown field rejected (client cannot inject a destination) --
def test_click_unknown_field_rejected():
    _ready_feed()
    s, b = adapi.request_delivery(6009, "feed", {"country": "us"})
    sp = b["sponsored"]
    did, tok = sp["delivery_id"], sp["impression_token"]
    adapi.record_impression(6009, did, {"token": tok, "placement": "feed"})
    s, b = adapi.record_click(6009, did, {"destination_ref": 999})
    _assert(s == 400 and b.get("code") == "unknown_field", (s, b))


# 10 -- admin read handlers return list/detail shapes ----------------------
def test_admin_read_shapes():
    _ready_feed()
    s, b = adapi.request_delivery(6010, "feed", {"country": "us"})
    sp = b["sponsored"]
    did, tok = sp["delivery_id"], sp["impression_token"]
    adapi.record_impression(6010, did, {"token": tok, "placement": "feed"})
    adapi.record_click(6010, did, {})
    conn = db.connect()
    try:
        row = deliv.load_delivery_row(conn, did)
    finally:
        conn.close()
    served_cid = row["campaign_id"]
    # list deliveries
    s, b = adapi.admin_list_deliveries(campaign_id=served_cid)
    _assert(s == 200 and b["ok"] is True and isinstance(b["deliveries"], list), (s, b))
    _assert(any(r["delivery_id"] == did for r in b["deliveries"]), b)
    # the client token is never exposed on the admin surface
    for r in b["deliveries"]:
        _assert("impression_token" not in r, r)
    # get one delivery
    s, b = adapi.admin_get_delivery(did)
    _assert(s == 200 and b["ok"] is True, (s, b))
    _assert(b["delivery"]["campaign"]["campaign_id"] == served_cid, b)
    _assert("impression_token" not in b["delivery"], b)
    # impressions + clicks lists
    s, b = adapi.admin_list_impressions(delivery_id=did)
    _assert(s == 200 and len(b["impressions"]) == 1, (s, b))
    s, b = adapi.admin_list_clicks(delivery_id=did)
    _assert(s == 200 and len(b["clicks"]) == 1, (s, b))


# 11 -- controller contract: every handler returns (int, dict{ok}) ---------
def test_controller_contract_shape():
    _ready_feed()
    s, b = adapi.request_delivery(6011, "feed", {"country": "us"})
    _assert(isinstance(s, int) and isinstance(b, dict) and "ok" in b, (s, b))
    # a no-token impression still obeys the (int, dict{ok}) contract
    s, b = adapi.record_impression(6011, "adlv_missing", {})
    _assert(isinstance(s, int) and isinstance(b, dict) and b["ok"] is False, (s, b))


# --- standalone runner ------------------------------------------------------
def _run_standalone():
    setup_module()
    tests = [
        test_all_handlers_dark_when_off,
        test_request_delivery_ok,
        test_request_delivery_unknown_field_rejected,
        test_request_delivery_bad_placement,
        test_impression_requires_token_then_ok,
        test_impression_unknown_field_rejected,
        test_impression_bad_token,
        test_click_flow,
        test_click_unknown_field_rejected,
        test_admin_read_shapes,
        test_controller_contract_shape,
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
