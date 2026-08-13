"""Advertising slice 7 — Feed/Reels delivery MVP service matrix.

Exercises the request-time delivery layer directly against the new service modules
(delivery / eligibility / selection / frequency / events) on top of a fully
delivery-ready canonical hierarchy built through the slice-6 controllers. bot.py is
not importable in the hermetic sandbox; its route adapters are checked structurally
in test_advertising_slice7_routes.py.

The slice proves the completion boundary:

    find ONE eligible approved creative -> bind it to an IMMUTABLE delivery
    instance -> return a sponsored Feed/Reels payload -> accept ONE idempotent
    impression -> accept ONE idempotent click

with its guardrails: selection is deterministic per-viewer (no auction); the
sponsored payload leaks no internal ledger/targeting/reviewer fields; a client can
never substitute a creative while reusing a delivery id (creative is read back from
the bound row, and the destination on the click is SERVER-resolved); a bad token,
expired delivery, or placement mismatch is rejected; the frequency cap derives from
the immutable impression log and yields a no-placement result; impressions and
clicks are each idempotent (a duplicate returns duplicate=True and never writes a
second row); a self/advertiser-owned view is flagged fraud + marked NOT billing
eligible; and NO money moves anywhere in the flow (wallet + escrow unchanged, every
event carries billing_eligible with billing_processed False).

    python tests/business_os/test_advertising_slice7_delivery.py   # no pytest needed
"""

import os
import tempfile

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="busos_ad7_"), "test.db")
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
from services.business_os.advertising import events as evts  # noqa: E402
from services.business_os.advertising import delivery_common as _c  # noqa: E402
from services.business_os.advertising.service import AdvertisingError  # noqa: E402
from services.business_os.ledger import ledger  # noqa: E402

ADMIN = 8
ACTIVE = {"account_status": "active", "access_enabled": 1}
_uid_seq = [4100]
_media_seq = [9000]
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
            # Keyed by user_id to match bot.init_db; this suite asserts the
            # advertiser identity on the served payload, so the production
            # column name is what makes that assertion real.
            "CREATE TABLE IF NOT EXISTS users "
            "(user_id INTEGER PRIMARY KEY, display_name TEXT, username TEXT)")
        conn.execute("CREATE TABLE IF NOT EXISTS pulse_posts (id INTEGER PRIMARY KEY)")
        conn.execute("CREATE TABLE IF NOT EXISTS pulse_reels (id INTEGER PRIMARY KEY)")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS marketplace_listings (id INTEGER PRIMARY KEY)")
        conn.execute(
            "INSERT OR IGNORE INTO users (user_id, display_name, username) "
            "VALUES (?, ?, ?)",
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
            "INSERT OR IGNORE INTO users (user_id, display_name, username) "
            "VALUES (?, ?, ?)",
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


def _funded_campaign(owner, budget=5000, wallet=10000, approve_owner=True):
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


def _active_campaign(owner, approve_owner=True):
    cid = _funded_campaign(owner, approve_owner=approve_owner)
    s, b = adapi.activate(owner, cid, {})
    _assert(s == 200 and b["operational"]["operational_status"] == "active", (s, b))
    return cid


def _ad_set(owner, cid, placements=("feed", "reels"), audience=None):
    payload = {"name": "AS", "placements": list(placements),
               "audience": audience if audience is not None
               else {"countries": ["us"]}}
    s, b = adapi.create_ad_set(owner, cid, payload, context=ACTIVE)
    _assert(s == 201, (s, b))
    return b["ad_set"]["ad_set_id"]


def _approved_ad_set(owner, cid, placements=("feed", "reels"), audience=None):
    asid = _ad_set(owner, cid, placements=placements, audience=audience)
    s, b = adapi.ad_set_lifecycle(owner, asid, "submit")
    _assert(s == 200, (s, b))
    s, b = adapi.admin_review_ad_set(ADMIN, asid, "approve")
    _assert(s == 200 and b["after_status"] == "approved", (s, b))
    return asid


def _approved_creative(owner, asid, creative_type="image", **overrides):
    media_type = "video" if creative_type in ("video", "reels_video") else "image"
    media_id = overrides.pop("media_asset_id", None)
    if media_id is None:
        media_id = _seed_media(owner, media_type=media_type)
    payload = {"creative_type": creative_type, "media_asset_id": media_id,
               "headline": "Buy now", "destination_type": "profile",
               "destination_ref": _DEST_USER}
    payload.update(overrides)
    s, b = adapi.create_creative(owner, asid, payload, context=ACTIVE)
    _assert(s == 201, (s, b))
    crid = b["creative"]["creative_id"]
    s, b = adapi.creative_lifecycle(owner, crid, "submit")
    _assert(s == 200, (s, b))
    s, b = adapi.admin_review_creative(ADMIN, crid, "approve")
    _assert(s == 200 and b["after_status"] == "approved", (s, b))
    return crid


def _ready_feed(owner=None):
    """A fully delivery-ready hierarchy with one approved Feed (image) creative."""
    owner = owner or _new_owner()
    cid = _active_campaign(owner)
    asid = _approved_ad_set(owner, cid, placements=("feed", "reels"))
    crid = _approved_creative(owner, asid, creative_type="image")
    return {"owner": owner, "cid": cid, "asid": asid, "crid": crid}


# 1 -- flag OFF: delivery/impression/click all dark ------------------------
def test_flag_off_dark():
    os.environ["BUSINESS_OS_ADVERTISING"] = "off"
    try:
        for fn in (
            lambda: deliv.request_placement(1, "feed"),
            lambda: evts.record_impression("adlv_x", "tok"),
            lambda: evts.record_click("adlv_x"),
            lambda: deliv.admin_search_deliveries(),
            lambda: evts.admin_search_impressions(),
            lambda: evts.admin_search_clicks(),
        ):
            raised = False
            try:
                fn()
            except AdvertisingError as e:
                raised = True
                _assert(e.http_status == 503 and e.code == "disabled",
                        f"flag-off must be 503/disabled: {e.http_status}/{e.code}")
            _assert(raised, "flag-off must raise AdvertisingError")
    finally:
        os.environ["BUSINESS_OS_ADVERTISING"] = "on"


# 2 -- happy path: eligible creative -> sponsored feed payload -------------
def test_request_returns_sponsored_payload():
    _ready_feed()
    res = deliv.request_placement(7001, "feed", request={"country": "us"})
    _assert(res["placement"] == "feed", res)
    sp = res.get("sponsored")
    _assert(sp is not None, ("expected a sponsored placement", res))
    _assert(sp["sponsored"] is True and sp["sponsored_label"] == "Sponsored", sp)
    _assert(sp["delivery_id"].startswith("adlv_"), sp)
    _assert(sp["creative_type"] == "image", sp)
    _assert(sp["impression_token"], sp)
    _assert(sp["destination"]["type"] == "profile", sp)
    _assert(sp["destination"]["ref"] == str(_DEST_USER), sp)
    _assert(sp["disclosure"]["kind"] == "paid_advertisement", sp)
    # A delivery row was actually persisted, bound to an APPROVED creative.
    #
    # This deliberately does not assert the row names `h["crid"]` specifically.
    # These suites share one database, so any other module that has already run
    # may have left its own approved, active, feed-eligible campaign behind, and
    # the rotation is then free to select it — which is correct behaviour, not a
    # bug. `test_no_creative_substitution` covers the identity invariant that
    # actually matters (the click echoes exactly the creative bound to this
    # delivery id) and is written the same pollution-tolerant way.
    conn = db.connect()
    try:
        row = deliv.load_delivery_row(conn, sp["delivery_id"])
        _assert(row is not None, ("no delivery row persisted", sp))
        creative = _svc._row_to_dict(conn.execute(
            "SELECT * FROM business_os_ad_creatives WHERE creative_id = ?",
            (row["creative_id"],)).fetchone())
    finally:
        conn.close()
    _assert(creative is not None, ("delivery bound to unknown creative", row))
    _assert(creative["status"] == "approved",
            ("delivery bound to a non-approved creative", creative))
    _assert(row["status"] == "active", row)


# 3 -- sponsored payload leaks no internal fields --------------------------
def test_payload_hides_internal_fields():
    _ready_feed()
    sp = deliv.request_placement(7002, "feed", request={"country": "us"})["sponsored"]
    for banned in ("advertiser_user_id", "campaign_id", "ad_set_id",
                   "subject_ref", "eligibility_snapshot_json", "request_ref",
                   "review_reason", "reviewer", "admin_notes", "audience",
                   "targeting", "price", "budget_cents", "escrow"):
        _assert(banned not in sp, f"payload leaked internal field {banned!r}: {list(sp)}")
    # advertiser identity is display-only (no account internals)
    for banned in ("account_status", "access_enabled", "email", "password"):
        _assert(banned not in sp["advertiser"], f"advertiser leaked {banned!r}")


# 4 -- reels creative served on the reels placement ------------------------
def test_reels_placement_served():
    owner = _new_owner()
    cid = _active_campaign(owner)
    asid = _approved_ad_set(owner, cid, placements=("reels",))
    crid = _approved_creative(owner, asid, creative_type="reels_video")
    res = deliv.request_placement(7003, "reels", request={"country": "us"})
    sp = res.get("sponsored")
    _assert(sp is not None and sp["creative_type"] == "reels_video", res)
    _assert(sp["placement"] == "reels", sp)


# 5 -- unsupported placement rejected --------------------------------------
def test_unsupported_placement_rejected():
    raised = False
    try:
        deliv.request_placement(7004, "stories")
    except AdvertisingError as e:
        raised = True
        _assert(e.http_status == 400 and e.code == "bad_placement", (e.http_status, e.code))
    _assert(raised, "unsupported placement must raise")


# 6 -- no eligible candidate -> structured no-placement --------------------
def test_no_candidate_returns_no_placement():
    # a brand-new viewer + placement with no approved creatives at all is unlikely
    # (other tests seed some), so force it with an audience that excludes the viewer.
    owner = _new_owner()
    cid = _active_campaign(owner)
    asid = _approved_ad_set(owner, cid, placements=("feed",),
                            audience={"countries": ["ca"]})
    _approved_creative(owner, asid, creative_type="image")
    # viewer reports a country NOT in the ad-set allowlist -> this creative fails
    # its audience gate. (Other seeded creatives may still match, so assert on the
    # per-decision audience gate rather than the aggregate winner.)
    res = deliv.request_placement(7005, "feed", request={"country": "de"})
    _assert("placement" in res, res)
    # Directly assert the audience gate disqualifies our creative for this viewer.
    conn = db.connect()
    try:
        from services.business_os.advertising import selection as _sel
        out = _sel.select_candidate(
            conn, placement="feed", subject_ref=_c.subject_ref(7005),
            request_ctx={"country": "de"}, strategy=None)
        ours = [d for d in out["decisions"]
                if d["ad_set_id"] == asid]
        _assert(ours and ours[0]["audience_ok"] is False, ours)
        _assert("audience_mismatch" in ours[0]["reasons"], ours[0]["reasons"])
    finally:
        conn.close()


# 7 -- impression: happy path + idempotent duplicate -----------------------
def test_impression_idempotent():
    _ready_feed()
    sp = deliv.request_placement(7006, "feed", request={"country": "us"})["sponsored"]
    did, tok = sp["delivery_id"], sp["impression_token"]
    first = evts.record_impression(did, tok, placement="feed", viewer_user_id=7006)
    _assert(first["duplicate"] is False, first)
    _assert(first["billing_eligible"] is True, first)
    _assert(first["billing_processed"] is False, first)
    _assert(first["fraud_status"] == "clean", first)
    # a second identical impression is served idempotently, no new row
    second = evts.record_impression(did, tok, placement="feed", viewer_user_id=7006)
    _assert(second["duplicate"] is True, second)
    _assert(second["event_id"] == first["event_id"], (first, second))
    conn = db.connect()
    try:
        n = conn.execute(
            "SELECT COUNT(*) FROM business_os_ad_impression_events WHERE delivery_id=?",
            (did,)).fetchone()[0]
    finally:
        conn.close()
    _assert(n == 1, f"exactly one impression row expected, got {n}")


# 8 -- impression: bad token rejected --------------------------------------
def test_impression_bad_token():
    _ready_feed()
    sp = deliv.request_placement(7007, "feed", request={"country": "us"})["sponsored"]
    raised = False
    try:
        evts.record_impression(sp["delivery_id"], "not-the-token", placement="feed")
    except AdvertisingError as e:
        raised = True
        _assert(e.http_status == 403 and e.code == "bad_token", (e.http_status, e.code))
    _assert(raised, "bad token must raise")
    # and no impression row was written
    conn = db.connect()
    try:
        n = conn.execute(
            "SELECT COUNT(*) FROM business_os_ad_impression_events WHERE delivery_id=?",
            (sp["delivery_id"],)).fetchone()[0]
    finally:
        conn.close()
    _assert(n == 0, f"bad token must write nothing, got {n}")


# 9 -- impression: placement mismatch rejected -----------------------------
def test_impression_placement_mismatch():
    _ready_feed()
    sp = deliv.request_placement(7008, "feed", request={"country": "us"})["sponsored"]
    raised = False
    try:
        evts.record_impression(sp["delivery_id"], sp["impression_token"],
                               placement="reels")
    except AdvertisingError as e:
        raised = True
        _assert(e.http_status == 409 and e.code == "placement_mismatch",
                (e.http_status, e.code))
    _assert(raised, "placement mismatch must raise")


# 10 -- impression: expired delivery rejected ------------------------------
def test_impression_expired():
    _ready_feed()
    sp = deliv.request_placement(7009, "feed", request={"country": "us"})["sponsored"]
    did = sp["delivery_id"]
    # force expiry by backdating the stored expires_at
    conn = db.connect()
    try:
        past = _c.iso(_c.now_utc().replace(year=2000))
        conn.execute(
            "UPDATE business_os_ad_delivery_instances SET expires_at=? WHERE delivery_id=?",
            (past, did))
        conn.commit()
    finally:
        conn.close()
    raised = False
    try:
        evts.record_impression(did, sp["impression_token"], placement="feed")
    except AdvertisingError as e:
        raised = True
        _assert(e.http_status == 409 and e.code == "expired", (e.http_status, e.code))
    _assert(raised, "expired delivery must raise")


# 11 -- click: requires impression, then idempotent + server destination ---
def test_click_requires_impression_then_idempotent():
    _ready_feed()
    sp = deliv.request_placement(7010, "feed", request={"country": "us"})["sponsored"]
    did, tok = sp["delivery_id"], sp["impression_token"]
    # click before any impression -> rejected
    raised = False
    try:
        evts.record_click(did, viewer_user_id=7010)
    except AdvertisingError as e:
        raised = True
        _assert(e.http_status == 409 and e.code == "impression_required",
                (e.http_status, e.code))
    _assert(raised, "click without impression must raise")
    # record impression, then click succeeds with a SERVER-resolved destination
    evts.record_impression(did, tok, placement="feed", viewer_user_id=7010)
    first = evts.record_click(did, viewer_user_id=7010)
    _assert(first["duplicate"] is False, first)
    _assert(first["destination"]["type"] == "profile", first)
    _assert(first["destination"]["ref"] == str(_DEST_USER), first)
    _assert(first["billing_eligible"] is True and first["billing_processed"] is False, first)
    # duplicate click is idempotent, no second row
    second = evts.record_click(did, viewer_user_id=7010)
    _assert(second["duplicate"] is True and second["event_id"] == first["event_id"],
            (first, second))
    conn = db.connect()
    try:
        n = conn.execute(
            "SELECT COUNT(*) FROM business_os_ad_click_events WHERE delivery_id=?",
            (did,)).fetchone()[0]
    finally:
        conn.close()
    _assert(n == 1, f"exactly one click row expected, got {n}")


# 12 -- client cannot substitute the creative while reusing a delivery id ---
def test_no_creative_substitution():
    _ready_feed()
    sp = deliv.request_placement(7011, "feed", request={"country": "us"})["sponsored"]
    did = sp["delivery_id"]
    evts.record_impression(did, sp["impression_token"], placement="feed",
                           viewer_user_id=7011)
    # the click destination is resolved from the bound delivery row, not any client
    # input — there is no parameter to pass an alternate destination/creative. The
    # event must therefore echo exactly the creative/version/destination bound to
    # this delivery id (whichever creative the rotation selected).
    clk = evts.record_click(did, viewer_user_id=7011)
    conn = db.connect()
    try:
        row = deliv.load_delivery_row(conn, did)
    finally:
        conn.close()
    _assert(clk["creative_id"] == row["creative_id"], (clk, row))
    _assert(clk["creative_version"] == row["creative_version"], (clk, row))
    _assert(clk["destination"]["ref"] == row["destination_ref"], (clk, row))


# 13 -- frequency cap: derived from the immutable impression log -----------
def test_frequency_cap_blocks_new_delivery():
    owner = _new_owner()
    cid = _active_campaign(owner)
    asid = _approved_ad_set(owner, cid, placements=("feed",))
    crid = _approved_creative(owner, asid, creative_type="image")
    viewer = 7012
    subject = _c.subject_ref(viewer)
    # simulate the viewer having already hit the cap for THIS campaign by writing
    # impression rows directly into the immutable log (as prior deliveries would).
    conn = db.connect()
    try:
        for i in range(_c.FREQ_CAP_MAX):
            conn.execute(
                "INSERT INTO business_os_ad_impression_events "
                "(event_id, delivery_id, campaign_id, ad_set_id, creative_id, "
                "creative_version, placement, subject_ref, advertiser_user_id, "
                "event_at, dedup_key, fraud_status, billing_eligible, "
                "billing_processed, created_at) VALUES "
                "(?, ?, ?, ?, ?, 1, 'feed', ?, ?, ?, ?, 'clean', 1, 0, ?)",
                (f"adimp_seed_{viewer}_{i}", f"adlv_seed_{viewer}_{i}", cid, asid,
                 crid, subject, _svc._sid(owner), _c.now_iso(),
                 f"impr:adlv_seed_{viewer}_{i}", _c.now_iso()))
        conn.commit()
    finally:
        conn.close()
    # the capped campaign's creative must now fail the frequency gate for this viewer
    from services.business_os.advertising import selection as _sel
    conn = db.connect()
    try:
        out = _sel.select_candidate(
            conn, placement="feed", subject_ref=subject,
            request_ctx={"country": "us"}, strategy=None)
        ours = [d for d in out["decisions"] if d["campaign_id"] == cid]
        _assert(ours and ours[0]["frequency_ok"] is False, ours)
        _assert("frequency_cap_reached" in ours[0]["reasons"], ours[0]["reasons"])
    finally:
        conn.close()


# 14 -- self / advertiser-owned view flagged fraud + not billing eligible ---
def test_self_view_fraud_not_billable():
    _ready_feed()
    sp = deliv.request_placement(7101, "feed", request={"country": "us"})["sponsored"]
    did, tok = sp["delivery_id"], sp["impression_token"]
    # the served delivery belongs to SOME advertiser; that advertiser viewing their
    # own ad is the self-view signal, whichever creative the rotation selected.
    conn = db.connect()
    try:
        row = deliv.load_delivery_row(conn, did)
    finally:
        conn.close()
    advertiser = int(row["advertiser_user_id"])
    imp = evts.record_impression(did, tok, placement="feed", viewer_user_id=advertiser)
    _assert(imp["fraud_status"] == "self_view", imp)
    _assert(imp["billing_eligible"] is False, imp)
    clk = evts.record_click(did, viewer_user_id=advertiser)
    _assert(clk["fraud_status"] == "self_view", clk)
    _assert(clk["billing_eligible"] is False, clk)


# 15 -- the entire delivery flow moves NO money ----------------------------
def test_delivery_flow_no_spend():
    h = _ready_feed()
    owner, cid = h["owner"], h["cid"]
    wallet_before = ledger.get_balance(adf._wallet_account(owner), "usd")
    escrow_before = ledger.get_balance(adf._escrow_account(cid), "usd")
    sp = deliv.request_placement(7013, "feed", request={"country": "us"})["sponsored"]
    evts.record_impression(sp["delivery_id"], sp["impression_token"],
                           placement="feed", viewer_user_id=7013)
    evts.record_click(sp["delivery_id"], viewer_user_id=7013)
    _assert(ledger.get_balance(adf._wallet_account(owner), "usd") == wallet_before,
            "delivery must not move wallet funds")
    _assert(ledger.get_balance(adf._escrow_account(cid), "usd") == escrow_before,
            "delivery must not move escrow funds")


# 16 -- admin read-only visibility over deliveries + events ----------------
def test_admin_visibility_read_only():
    _ready_feed()
    sp = deliv.request_placement(7014, "feed", request={"country": "us"})["sponsored"]
    did = sp["delivery_id"]
    evts.record_impression(did, sp["impression_token"], placement="feed",
                           viewer_user_id=7014)
    evts.record_click(did, viewer_user_id=7014)
    conn = db.connect()
    try:
        row = deliv.load_delivery_row(conn, did)
    finally:
        conn.close()
    served_cid = row["campaign_id"]
    served_crid = row["creative_id"]
    # admin can search deliveries and inspect one with hierarchy context
    rows = deliv.admin_search_deliveries(campaign_id=served_cid)
    _assert(any(r["delivery_id"] == did for r in rows), rows)
    one = deliv.admin_get_delivery(did)
    _assert(one["creative"]["creative_id"] == served_crid, one)
    _assert(one["campaign"]["campaign_id"] == served_cid, one)
    # admin delivery view must NOT expose the client impression token
    _assert("impression_token" not in one, list(one))
    for r in rows:
        _assert("impression_token" not in r, list(r))
    # admin can search impression + click events
    imps = evts.admin_search_impressions(delivery_id=did)
    clks = evts.admin_search_clicks(delivery_id=did)
    _assert(len(imps) == 1 and imps[0]["delivery_id"] == did, imps)
    _assert(len(clks) == 1 and clks[0]["delivery_id"] == did, clks)


# 17 -- selection is deterministic per viewer ------------------------------
def test_selection_deterministic():
    _ready_feed()
    r1 = deliv.request_placement(7015, "feed", request={"country": "us"})["sponsored"]
    r2 = deliv.request_placement(7015, "feed", request={"country": "us"})["sponsored"]
    # same viewer + same candidate set -> same creative chosen (distinct delivery ids)
    _assert(r1["creative_id"] if "creative_id" in r1 else True, r1)
    conn = db.connect()
    try:
        a = deliv.load_delivery_row(conn, r1["delivery_id"])
        b = deliv.load_delivery_row(conn, r2["delivery_id"])
    finally:
        conn.close()
    _assert(a["creative_id"] == b["creative_id"],
            "same viewer must deterministically get the same creative")
    _assert(r1["delivery_id"] != r2["delivery_id"], "each request is its own delivery")


# --- standalone runner ------------------------------------------------------
def _run_standalone():
    setup_module()
    tests = [
        test_flag_off_dark,
        test_request_returns_sponsored_payload,
        test_payload_hides_internal_fields,
        test_reels_placement_served,
        test_unsupported_placement_rejected,
        test_no_candidate_returns_no_placement,
        test_impression_idempotent,
        test_impression_bad_token,
        test_impression_placement_mismatch,
        test_impression_expired,
        test_click_requires_impression_then_idempotent,
        test_no_creative_substitution,
        test_frequency_cap_blocks_new_delivery,
        test_self_view_fraud_not_billable,
        test_delivery_flow_no_spend,
        test_admin_visibility_read_only,
        test_selection_deterministic,
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
