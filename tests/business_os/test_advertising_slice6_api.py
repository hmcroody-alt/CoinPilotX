"""Advertising slice 6 — ad-set + creative foundation controller matrix.

Exercises the canonical hierarchy ``advertiser -> campaign -> ad set -> creative``
directly against the importable controller (services/business_os/advertising/api.py)
plus the ad-set service, the creative service (with authoritative media +
destination validation), and the DERIVED hierarchy-readiness composer. bot.py is
not importable in the hermetic sandbox; its route adapters are checked
structurally in test_advertising_slice6_routes.py.

The slice keeps every review status SEPARATE — campaign review / funding /
operational, ad-set review, creative review — and proves the completion boundary:

    owned campaign -> governed ad set -> authoritative creative -> submit ->
    admin approve/reject -> backend DERIVES delivery-readiness

with its guardrails: a child is only ever created under an owned, non-archived
parent (cross-owner / cross-campaign => 404); governed audience rejects unknown,
sensitive, and out-of-range fields; placements are a strict Feed/Reels allowlist;
media is validated against the authoritative ownership system (cross-user =>
media_not_found, never leaked); destinations are internal-verified or HTTPS-only;
advertisers can never self-approve; a submitted creative is immutable in place and
must be revised into a NEW version; admin decisions are audited and the rejection
reason is visible to the owner; parent archival blocks submission; readiness keeps
its inputs separate and is never stored; approval delivers/spends nothing; and the
whole surface goes dark (404) when the flag is off while legacy tables are never
touched.

    python tests/business_os/test_advertising_slice6_api.py   # no pytest needed
"""

import os
import tempfile

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="busos_ad6_"), "test.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB
os.environ["BUSINESS_OS_ADVERTISING"] = "on"

import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from services import db  # noqa: E402
from services.business_os.advertising import service as ad  # noqa: E402
from services.business_os.advertising import service as _svc  # noqa: E402
from services.business_os.advertising import funding as adf  # noqa: E402
from services.business_os.advertising import api as adapi  # noqa: E402
from services.business_os.ledger import ledger  # noqa: E402

ADMIN = 8
ACTIVE = {"account_status": "active", "access_enabled": 1}
_uid_seq = [2100]
_media_seq = [7000]
_DEST_USER = 555  # a seeded internal profile-destination target


def setup_module(module=None):
    ad.ensure_schema()
    ledger.ensure_schema()
    conn = db.connect()
    try:
        # Authoritative tables consulted by media + destination validation. These
        # already exist in production; the hermetic DB seeds a minimal shape.
        conn.execute(
            "CREATE TABLE IF NOT EXISTS pulse_media_assets "
            "(id INTEGER PRIMARY KEY, owner_user_id TEXT, media_type TEXT, "
            "processing_status TEXT)")
        # `users` is keyed by user_id in bot.init_db, not id. Seeding an `id`
        # column invented a shape production does not have: it hid the delivery
        # bug where the advertiser identity lookup selected on `users.id`, and
        # because these are CREATE IF NOT EXISTS against a shared test database
        # it also collided with every other suite's `users` table whenever the
        # full run ordered this file first.
        conn.execute("CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY)")
        conn.execute("CREATE TABLE IF NOT EXISTS pulse_posts (id INTEGER PRIMARY KEY)")
        conn.execute("CREATE TABLE IF NOT EXISTS pulse_reels (id INTEGER PRIMARY KEY)")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS marketplace_listings (id INTEGER PRIMARY KEY)")
        conn.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (_DEST_USER,))
        conn.commit()
    finally:
        conn.close()


def _new_owner():
    _uid_seq[0] += 1
    return _uid_seq[0]


def _assert(cond, msg):
    if not cond:
        raise AssertionError(msg)


def _seed_media(owner, media_type="image", status="ready"):
    """Insert one authoritative media asset owned by ``owner``; return its id."""
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
    """approved + funded + operationally ACTIVE (the campaign side of a fully
    delivery-ready hierarchy)."""
    cid = _funded_campaign(owner, approve_owner=approve_owner)
    s, b = adapi.activate(owner, cid, {})
    _assert(s == 200 and b["operational"]["operational_status"] == "active", (s, b))
    return cid


def _ad_set(owner, cid, placements=("feed", "reels"), audience=None):
    payload = {"name": "AS", "placements": list(placements),
               "audience": audience if audience is not None
               else {"countries": ["us"], "min_age": 21, "max_age": 45}}
    s, b = adapi.create_ad_set(owner, cid, payload, context=ACTIVE)
    _assert(s == 201, (s, b))
    return b["ad_set"]["ad_set_id"]


def _approved_ad_set(owner, cid):
    asid = _ad_set(owner, cid)
    s, b = adapi.ad_set_lifecycle(owner, asid, "submit")
    _assert(s == 200, (s, b))
    s, b = adapi.admin_review_ad_set(ADMIN, asid, "approve")
    _assert(s == 200 and b["after_status"] == "approved", (s, b))
    return asid


def _creative(owner, asid, **overrides):
    media_id = overrides.pop("media_asset_id", None)
    if media_id is None:
        media_id = _seed_media(owner)
    payload = {"creative_type": "image", "media_asset_id": media_id,
               "headline": "Hello", "destination_type": "profile",
               "destination_ref": _DEST_USER}
    payload.update(overrides)
    s, b = adapi.create_creative(owner, asid, payload, context=ACTIVE)
    _assert(s == 201, (s, b))
    return b["creative"]["creative_id"]


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


# 1 -- flag OFF: every slice-6 handler is dark (404) -------------------------
def test_flag_off_dark():
    os.environ["BUSINESS_OS_ADVERTISING"] = "off"
    for result in (
        adapi.create_ad_set(1, "nope", {}),
        adapi.list_ad_sets(1),
        adapi.get_ad_set(1, "nope"),
        adapi.update_ad_set(1, "nope", {}),
        adapi.ad_set_lifecycle(1, "nope", "submit"),
        adapi.create_creative(1, "nope", {}),
        adapi.list_creatives(1),
        adapi.get_creative(1, "nope"),
        adapi.update_creative(1, "nope", {}),
        adapi.revise_creative(1, "nope", {}),
        adapi.creative_lifecycle(1, "nope", "submit"),
        adapi.get_creative_readiness(1, "nope"),
        adapi.admin_list_ad_sets(),
        adapi.admin_get_ad_set("nope"),
        adapi.admin_review_ad_set(ADMIN, "nope", "approve"),
        adapi.admin_list_creatives(),
        adapi.admin_get_creative("nope"),
        adapi.admin_review_creative(ADMIN, "nope", "approve"),
        adapi.admin_get_creative_readiness("nope"),
    ):
        status, body = result
        _assert(status == 404 and body["ok"] is False, f"expected dark 404, got {result}")
    os.environ["BUSINESS_OS_ADVERTISING"] = "on"


# 2 -- owned ad-set + creative creation under an owned parent succeed --------
def test_owned_creation_succeeds():
    owner = _new_owner()
    cid = _funded_campaign(owner)
    s, b = adapi.create_ad_set(
        owner, cid, {"name": "Set A", "placements": ["feed"],
                     "audience": {"countries": ["us"]}}, context=ACTIVE)
    _assert(s == 201, (s, b))
    aset = b["ad_set"]
    _assert(aset["status"] == "draft", aset)
    _assert(aset["campaign_id"] == cid, aset)
    _assert(aset["advertiser_user_id"] == _svc._sid(owner), aset)
    asid = aset["ad_set_id"]
    media = _seed_media(owner)
    s, b = adapi.create_creative(
        owner, asid, {"creative_type": "image", "media_asset_id": media},
        context=ACTIVE)
    _assert(s == 201, (s, b))
    cr = b["creative"]
    _assert(cr["status"] == "draft", cr)
    _assert(cr["ad_set_id"] == asid and cr["campaign_id"] == cid, cr)
    _assert(cr["media_asset_id"] == str(media), cr)


# 3 -- non-owner cannot read/edit a child (404, existence not leaked) --------
def test_non_owner_child_404():
    owner = _new_owner()
    cid = _funded_campaign(owner)
    asid = _ad_set(owner, cid)
    crid = _creative(owner, asid)
    other = _new_owner()
    _approve(other)
    for result in (
        adapi.get_ad_set(other, asid),
        adapi.update_ad_set(other, asid, {"name": "x"}),
        adapi.ad_set_lifecycle(other, asid, "submit"),
        adapi.create_creative(other, asid, {"creative_type": "image"}),
        adapi.get_creative(other, crid),
        adapi.update_creative(other, crid, {"headline": "x"}),
        adapi.creative_lifecycle(other, crid, "submit"),
        adapi.get_creative_readiness(other, crid),
    ):
        s, b = result
        _assert(s == 404 and b["code"] == "not_found",
                f"non-owner must get 404 not_found: {result}")


# 4 -- cross-campaign / cross-owner child rejected ---------------------------
def test_cross_owner_parent_rejected():
    owner = _new_owner()
    cid = _funded_campaign(owner)
    intruder = _new_owner()
    _approve(intruder)
    # intruder tries to hang an ad set off the owner's campaign
    s, b = adapi.create_ad_set(
        intruder, cid, {"name": "X", "placements": ["feed"],
                        "audience": {"countries": ["us"]}}, context=ACTIVE)
    _assert(s == 404 and b["code"] == "not_found", (s, b))
    # a creative cannot be created under the owner's ad set by the intruder either
    asid = _ad_set(owner, cid)
    s, b = adapi.create_creative(
        intruder, asid, {"creative_type": "image"}, context=ACTIVE)
    _assert(s == 404 and b["code"] == "not_found", (s, b))


# 5 -- governed audience: unknown field rejected ----------------------------
def test_unknown_targeting_field_rejected():
    owner = _new_owner()
    cid = _funded_campaign(owner)
    s, b = adapi.create_ad_set(
        owner, cid, {"name": "AS", "placements": ["feed"],
                     "audience": {"countries": ["us"], "favorite_color": ["blue"]}},
        context=ACTIVE)
    _assert(s == 400 and b["code"] == "unknown_targeting_field", (s, b))


# 6 -- governed audience: sensitive / prohibited category rejected ----------
def test_sensitive_targeting_rejected():
    owner = _new_owner()
    cid = _funded_campaign(owner)
    for banned in ({"religion": ["x"]}, {"race": ["x"]}, {"lookalike": True},
                   {"precise_location": {"lat": 1, "lng": 2}}, {"children": True}):
        aud = {"countries": ["us"]}
        aud.update(banned)
        s, b = adapi.create_ad_set(
            owner, cid, {"name": "AS", "placements": ["feed"], "audience": aud},
            context=ACTIVE)
        _assert(s == 400 and b["code"] in ("prohibited_targeting",
                                           "unknown_targeting_field"),
                (banned, s, b))


# 7 -- governed audience: invalid age range rejected ------------------------
def test_invalid_age_range_rejected():
    owner = _new_owner()
    cid = _funded_campaign(owner)
    s, b = adapi.create_ad_set(
        owner, cid, {"name": "AS", "placements": ["feed"],
                     "audience": {"min_age": 40, "max_age": 20}}, context=ACTIVE)
    _assert(s == 400 and b["code"] == "bad_age_range", (s, b))
    # under-floor age (targeting minors) is rejected too
    s, b = adapi.create_ad_set(
        owner, cid, {"name": "AS", "placements": ["feed"],
                     "audience": {"min_age": 10, "max_age": 20}}, context=ACTIVE)
    _assert(s == 400 and b["code"] == "bad_age_range", (s, b))


# 8 -- placement allowlist: unsupported rejected, Feed + Reels accepted ------
def test_placement_allowlist():
    owner = _new_owner()
    cid = _funded_campaign(owner)
    s, b = adapi.create_ad_set(
        owner, cid, {"name": "AS", "placements": ["stories"],
                     "audience": {"countries": ["us"]}}, context=ACTIVE)
    _assert(s == 400 and b["code"] == "unsupported_placement", (s, b))
    # valid Feed
    s, b = adapi.create_ad_set(
        owner, cid, {"name": "Feed", "placements": ["feed"],
                     "audience": {"countries": ["us"]}}, context=ACTIVE)
    _assert(s == 201 and b["ad_set"]["placement_valid"] is True, (s, b))
    # valid Reels
    s, b = adapi.create_ad_set(
        owner, cid, {"name": "Reels", "placements": ["reels"],
                     "audience": {"countries": ["us"]}}, context=ACTIVE)
    _assert(s == 201 and b["ad_set"]["placement_valid"] is True, (s, b))


# 9 -- creative media: owned accepted; missing + cross-user rejected --------
def test_creative_media_ownership():
    owner = _new_owner()
    cid = _funded_campaign(owner)
    asid = _ad_set(owner, cid)
    # owned media accepted
    my_media = _seed_media(owner)
    s, b = adapi.create_creative(
        owner, asid, {"creative_type": "image", "media_asset_id": my_media},
        context=ACTIVE)
    _assert(s == 201 and b["creative"]["media_asset_id"] == str(my_media), (s, b))
    # media owned by a different advertiser -> media_not_found (never leaked)
    other = _new_owner()
    their_media = _seed_media(other)
    s, b = adapi.create_creative(
        owner, asid, {"creative_type": "image", "media_asset_id": their_media},
        context=ACTIVE)
    _assert(s == 404 and b["code"] == "media_not_found", (s, b))
    # a raw path (not a canonical id) is rejected outright
    s, b = adapi.create_creative(
        owner, asid, {"creative_type": "image",
                      "media_asset_id": "/uploads/pic.jpg"}, context=ACTIVE)
    _assert(s == 400 and b["code"] == "bad_media_ref", (s, b))
    # submitting a creative with NO media fails the completeness contract
    crid = _creative(owner, asid, media_asset_id=my_media)
    # withdraw media by making a fresh draft with none, then submit
    s, b = adapi.create_creative(
        owner, asid, {"creative_type": "image", "destination_type": "profile",
                      "destination_ref": _DEST_USER}, context=ACTIVE)
    _assert(s == 201, (s, b))
    empty = b["creative"]["creative_id"]
    s, b = adapi.creative_lifecycle(owner, empty, "submit")
    _assert(s == 400 and b["code"] == "missing_media", (s, b))


# 10 -- destination: HTTPS-only external, internal canonical id verified -----
def test_destination_validation():
    owner = _new_owner()
    cid = _funded_campaign(owner)
    asid = _ad_set(owner, cid)
    media = _seed_media(owner)
    # non-HTTPS external rejected
    s, b = adapi.create_creative(
        owner, asid, {"creative_type": "image", "media_asset_id": media,
                      "destination_type": "external",
                      "destination_ref": "http://insecure.example"}, context=ACTIVE)
    _assert(s == 400 and b["code"] == "bad_destination_scheme", (s, b))
    # internal destination pointing at a non-existent canonical id rejected
    s, b = adapi.create_creative(
        owner, asid, {"creative_type": "image", "media_asset_id": media,
                      "destination_type": "profile",
                      "destination_ref": 999999}, context=ACTIVE)
    _assert(s == 404 and b["code"] == "destination_not_found", (s, b))
    # internal destination to a verified canonical id accepted + stored
    s, b = adapi.create_creative(
        owner, asid, {"creative_type": "image", "media_asset_id": media,
                      "destination_type": "profile",
                      "destination_ref": _DEST_USER}, context=ACTIVE)
    _assert(s == 201, (s, b))
    _assert(b["creative"]["destination_type"] == "profile", b)
    _assert(b["creative"]["destination_ref"] == str(_DEST_USER), b)
    # external HTTPS is normalized + accepted
    s, b = adapi.create_creative(
        owner, asid, {"creative_type": "image", "media_asset_id": media,
                      "destination_type": "external",
                      "destination_ref": "HTTPS://Example.COM/Path?a=1#frag"},
        context=ACTIVE)
    _assert(s == 201, (s, b))
    _assert(b["creative"]["destination_ref"] == "https://example.com/Path?a=1", b)


# 11 -- advertiser cannot directly approve (no approve verb) -----------------
def test_advertiser_cannot_approve():
    owner = _new_owner()
    cid = _funded_campaign(owner)
    asid = _ad_set(owner, cid)
    s, b = adapi.ad_set_lifecycle(owner, asid, "approve")
    _assert(s == 400 and b["code"] == "bad_action", (s, b))
    crid = _creative(owner, asid)
    s, b = adapi.creative_lifecycle(owner, crid, "approve")
    _assert(s == 400 and b["code"] == "bad_action", (s, b))
    # a raw-status injection through update is rejected as an unknown field
    s, b = adapi.update_creative(owner, crid, {"status": "approved"})
    _assert(s == 400 and b["code"] == "unknown_field", (s, b))


# 12 -- a submitted creative cannot be silently edited in place -------------
def test_submitted_creative_immutable():
    owner = _new_owner()
    cid = _funded_campaign(owner)
    asid = _approved_ad_set(owner, cid)
    crid = _creative(owner, asid)
    s, b = adapi.creative_lifecycle(owner, crid, "submit")
    _assert(s == 200 and b["creative"]["status"] == "submitted", (s, b))
    s, b = adapi.update_creative(owner, crid, {"headline": "Sneaky edit"})
    _assert(s == 409 and b["code"] == "not_editable", (s, b))


# 13 -- a material revision spawns a NEW version, original intact -----------
def test_material_revision_new_version():
    owner = _new_owner()
    cid = _funded_campaign(owner)
    asid = _approved_ad_set(owner, cid)
    crid = _creative(owner, asid, headline="Original")
    _assert(adapi.creative_lifecycle(owner, crid, "submit")[0] == 200, "submit")
    _assert(adapi.admin_review_creative(ADMIN, crid, "approve")[0] == 200, "approve")
    orig = adapi.get_creative(owner, crid)[1]["creative"]
    # a revision with no material change is rejected
    s, b = adapi.revise_creative(owner, crid, {"accessibility_text": "alt only"})
    _assert(s == 400 and b["code"] == "no_material_change", (s, b))
    # a material change produces a new draft version linked to the original
    s, b = adapi.revise_creative(owner, crid, {"headline": "Revised"})
    _assert(s == 201, (s, b))
    new = b["creative"]
    _assert(new["creative_id"] != crid, new)
    _assert(new["status"] == "draft", new)
    _assert(new["supersedes_creative_id"] == crid, new)
    _assert(new["version"] == int(orig["version"]) + 1, (new, orig))
    _assert(new["headline"] == "Revised", new)
    # the reviewed original is untouched (still approved)
    still = adapi.get_creative(owner, crid)[1]["creative"]
    _assert(still["status"] == "approved", still)
    _assert(still["headline"] == "Original", still)


# 14 -- admin approve/reject audited; rejection reason visible to owner ------
def test_admin_review_audited_and_reason_visible():
    owner = _new_owner()
    cid = _funded_campaign(owner)
    asid = _ad_set(owner, cid)
    _assert(adapi.ad_set_lifecycle(owner, asid, "submit")[0] == 200, "submit set")
    _assert(adapi.admin_review_ad_set(ADMIN, asid, "approve")[0] == 200, "approve set")
    # creative reject with a structured reason
    crid = _creative(owner, asid)
    _assert(adapi.creative_lifecycle(owner, crid, "submit")[0] == 200, "submit cr")
    s, b = adapi.admin_review_creative(
        ADMIN, crid, "reject", reason="Headline violates policy 4.2")
    _assert(s == 200 and b["after_status"] == "rejected", (s, b))
    _assert(b["before_status"] == "submitted", b)
    # rejection reason is visible to the owner
    view = adapi.get_creative(owner, crid)[1]["creative"]
    _assert(view["status"] == "rejected", view)
    _assert(view["review_reason"] == "Headline violates policy 4.2", view)
    # a rejection with NO reason is refused
    owner2 = _new_owner()
    cid2 = _funded_campaign(owner2)
    asid2 = _ad_set(owner2, cid2)
    crid2 = _creative(owner2, asid2)
    _assert(adapi.creative_lifecycle(owner2, crid2, "submit")[0] == 200, "submit cr2")
    s, b = adapi.admin_review_creative(ADMIN, crid2, "reject", reason="")
    _assert(s == 400 and b["code"] == "reason_required", (s, b))
    # durable audit trail recorded both admin decisions with the acting admin
    actions = {r["action"] for r in _audit_rows(cid)}
    _assert("ad_set_approved" in actions, actions)
    _assert("creative_rejected" in actions, actions)
    reject_rows = [r for r in _audit_rows(cid) if r["action"] == "creative_rejected"]
    for r in reject_rows:
        _assert(str(ADMIN) in str(r["actor"]) or r["actor"] == ADMIN, r)


# 15 -- parent archival blocks submission of children -----------------------
def test_parent_archival_blocks_submission():
    owner = _new_owner()
    cid = _funded_campaign(owner)
    asid = _ad_set(owner, cid)
    crid = _creative(owner, asid)  # created while parent live
    # archive the ad set, then the child creative can no longer be submitted
    _assert(adapi.ad_set_lifecycle(owner, asid, "archive")[0] == 200, "archive set")
    s, b = adapi.creative_lifecycle(owner, crid, "submit")
    _assert(s == 409 and b["code"] == "parent_archived", (s, b))
    # a NEW child cannot even be created under an archived ad set
    s, b = adapi.create_creative(
        owner, asid, {"creative_type": "image"}, context=ACTIVE)
    _assert(s == 409 and b["code"] == "parent_archived", (s, b))
    # and an ad set cannot be added to an archived campaign
    owner2 = _new_owner()
    _approve(owner2)
    cid2 = _draft(owner2)
    _assert(adapi.lifecycle(owner2, cid2, "archive")[0] == 200, "archive campaign")
    s, b = adapi.create_ad_set(
        owner2, cid2, {"name": "AS", "placements": ["feed"],
                       "audience": {"countries": ["us"]}}, context=ACTIVE)
    _assert(s == 409 and b["code"] == "parent_archived", (s, b))


# 16 -- readiness DERIVED live from SEPARATE inputs; not stored -------------
def test_readiness_keeps_inputs_separate():
    owner = _new_owner()
    cid = _active_campaign(owner)
    asid = _approved_ad_set(owner, cid)
    crid = _creative(owner, asid)
    # before creative approval: not ready, blocked precisely on the creative
    s, b = adapi.get_creative_readiness(owner, crid)
    _assert(s == 200, (s, b))
    view = b["readiness"]
    SEP = ("campaign_review_approved", "campaign_funded",
           "campaign_operational_active", "ad_set_approved", "creative_approved",
           "placement_valid", "audience_valid")
    for k in SEP:
        _assert(k in view, f"readiness must expose separate input {k}: {view}")
    _assert(view["hierarchy_ready"] is False, view)
    _assert(view["creative_approved"] is False, view)
    _assert("creative_not_approved" in view["denial_reasons"], view)
    # every other input is already satisfied and shown separately
    _assert(view["campaign_review_approved"] is True, view)
    _assert(view["campaign_funded"] is True, view)
    _assert(view["campaign_operational_active"] is True, view)
    _assert(view["ad_set_approved"] is True, view)
    _assert(view["placement_valid"] is True and view["audience_valid"] is True, view)
    # approve the creative -> now the whole hierarchy is derived-ready
    _assert(adapi.creative_lifecycle(owner, crid, "submit")[0] == 200, "submit")
    _assert(adapi.admin_review_creative(ADMIN, crid, "approve")[0] == 200, "approve")
    s, b = adapi.get_creative_readiness(owner, crid)
    view = b["readiness"]
    _assert(view["hierarchy_ready"] is True, view)
    _assert(view["denial_reasons"] == [], view)
    _assert(all(view[k] is True for k in SEP), view)
    # readiness is never persisted: no such column on the creative row
    conn = db.connect()
    try:
        cols = {r[1] for r in conn.execute(
            "PRAGMA table_info(business_os_ad_creatives)").fetchall()}
    finally:
        conn.close()
    _assert("hierarchy_ready" not in cols, cols)
    _assert("readiness" not in cols, cols)


# 17 -- an approved creative causes NO delivery and NO spend ----------------
def test_approved_creative_no_delivery_no_spend():
    owner = _new_owner()
    cid = _active_campaign(owner)
    asid = _approved_ad_set(owner, cid)
    crid = _creative(owner, asid)
    wallet_before = ledger.get_balance(adf._wallet_account(owner), "usd")
    escrow_before = ledger.get_balance(adf._escrow_account(cid), "usd")
    _assert(adapi.creative_lifecycle(owner, crid, "submit")[0] == 200, "submit")
    _assert(adapi.admin_review_creative(ADMIN, crid, "approve")[0] == 200, "approve")
    # no money moved by review/approval
    _assert(ledger.get_balance(adf._wallet_account(owner), "usd") == wallet_before,
            "approval must not move wallet funds")
    _assert(ledger.get_balance(adf._escrow_account(cid), "usd") == escrow_before,
            "approval must not move escrow funds")
    # readiness view never claims to be delivering and leaks no delivery fields
    view = adapi.get_creative_readiness(owner, crid)[1]["readiness"]
    _assert(view["delivering"] is False, view)
    for banned in ("impressions", "clicks", "spend", "auction", "served",
                   "views", "delivered", "pacing"):
        _assert(banned not in view, f"readiness leaked delivery field {banned!r}")
    # no legacy delivery table is ever created by the canonical creative path
    conn = db.connect()
    try:
        legacy = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND "
            "name IN ('pulse_ad_campaigns','pulse_ad_creatives','pulse_ad_impressions')"
        ).fetchall()
    finally:
        conn.close()
    _assert(legacy == [], f"canonical path must not create legacy tables: {legacy}")


# 18 -- admin queue + combined read across owners ---------------------------
def test_admin_queue_and_combined_read():
    owner = _new_owner()
    cid = _funded_campaign(owner)
    asid = _ad_set(owner, cid)
    crid = _creative(owner, asid)
    _assert(adapi.creative_lifecycle(owner, crid, "submit")[0] == 200, "submit")
    # default admin creative queue shows submitted creatives
    s, b = adapi.admin_list_creatives()
    _assert(s == 200 and any(r["creative_id"] == crid for r in b["creatives"]), b)
    # admin combined read exposes ad-set + campaign context
    s, b = adapi.admin_get_creative(crid)
    _assert(s == 200, (s, b))
    _assert(b["creative"]["ad_set"]["ad_set_id"] == asid, b)
    _assert(b["creative"]["campaign"]["campaign_id"] == cid, b)
    # unknown admin status filter rejected
    s, b = adapi.admin_list_creatives(status="bogus")
    _assert(s == 400 and b["code"] == "bad_status", (s, b))
    # admin readiness is trusted (no owner scoping) and derives the same view
    s, b = adapi.admin_get_creative_readiness(crid)
    _assert(s == 200 and b["readiness"]["creative_id"] == crid, (s, b))


# 19 -- unknown fields rejected on create/update ----------------------------
def test_unknown_fields_rejected():
    owner = _new_owner()
    cid = _funded_campaign(owner)
    s, b = adapi.create_ad_set(
        owner, cid, {"name": "AS", "placements": ["feed"], "status": "approved"},
        context=ACTIVE)
    _assert(s == 400 and b["code"] == "unknown_field", (s, b))
    asid = _ad_set(owner, cid)
    s, b = adapi.create_creative(
        owner, asid, {"creative_type": "image", "delivering": True}, context=ACTIVE)
    _assert(s == 400 and b["code"] == "unknown_field", (s, b))


# --- standalone runner ------------------------------------------------------
def _run_standalone():
    setup_module()
    tests = [
        test_flag_off_dark,
        test_owned_creation_succeeds,
        test_non_owner_child_404,
        test_cross_owner_parent_rejected,
        test_unknown_targeting_field_rejected,
        test_sensitive_targeting_rejected,
        test_invalid_age_range_rejected,
        test_placement_allowlist,
        test_creative_media_ownership,
        test_destination_validation,
        test_advertiser_cannot_approve,
        test_submitted_creative_immutable,
        test_material_revision_new_version,
        test_admin_review_audited_and_reason_visible,
        test_parent_archival_blocks_submission,
        test_readiness_keeps_inputs_separate,
        test_approved_creative_no_delivery_no_spend,
        test_admin_queue_and_combined_read,
        test_unknown_fields_rejected,
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
