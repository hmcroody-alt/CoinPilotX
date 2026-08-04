"""Business OS — the seller business profile, exercised directly.

These tests are the executable form of the defects the brief listed. Each one names
the symptom it prevents from coming back:

  * ``@@Pilot-8919``            -> test_handle_is_normalised_once
  * "in review" AND "Approved"  -> test_verification_has_one_authoritative_state
  * whole profile frozen        -> test_review_narrows_to_identity_fields_only
  * one typo loses five edits   -> test_partial_save_keeps_the_valid_fields
  * "Individual" as a category  -> test_seller_type_is_not_a_business_category
  * "Halfway there."            -> test_completeness_itemises_what_is_missing
  * "coming soon" in production -> test_hours_distinguish_unset_from_closed
  * private data in the preview -> test_public_profile_publishes_nothing_private

    python3 tests/business_os/test_seller_profile.py   # pytest is not installed
"""

import os
import tempfile

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="busos_profile_"), "test.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB

import sys  # noqa: E402
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from services import db  # noqa: E402
from services.business_os.profile import api, schema, service as svc  # noqa: E402
from services.business_os.profile.service import ProfileError  # noqa: E402


SELLER = 7001          # the ordinary seller most tests use
APPROVED = 7002        # a business whose *business* verification passed
BADGED = 7003          # an account with a personal blue badge, business unreviewed
SUSPENDED = 7004       # enforcement review
FRESH = 7005           # never touched anything


def _fixture_tables():
    """Stand in for the two tables bot.init_db owns, so the service can read them.

    Only the columns this module reads are declared. Declaring the real 60-column
    ``users`` table here would make this test a copy of a schema it does not own, and
    it would rot the first time that schema changed.
    """
    conn = db.connect()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                avatar_url TEXT,
                avatar_thumbnail_url TEXT,
                verified_badge INTEGER DEFAULT 0,
                created_at TEXT
            )""")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS marketplace_merchant_applications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                status TEXT,
                seller_type TEXT,
                business_name TEXT,
                business_description TEXT,
                pulse_username TEXT,
                email TEXT,
                phone TEXT,
                website TEXT,
                state_region TEXT,
                country TEXT,
                created_at TEXT
            )""")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS verification_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                verification_type TEXT,
                status TEXT,
                decided_at TEXT,
                admin_note TEXT,
                created_at TEXT
            )""")

        conn.execute(
            "INSERT INTO users (user_id, username, verified_badge, created_at) "
            "VALUES (?, ?, ?, ?)", (SELLER, "Pilot-8919", 0, "2024-03-02T00:00:00Z"))
        conn.execute(
            "INSERT INTO users (user_id, username, avatar_url, verified_badge, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (APPROVED, "approved-shop", "https://cdn/x.png", 1, "2023-01-05T00:00:00Z"))
        # The exact shape that produced the contradiction: a *personal* badge on an
        # account whose *business* application has not been looked at.
        conn.execute(
            "INSERT INTO users (user_id, username, verified_badge, created_at) "
            "VALUES (?, ?, ?, ?)", (BADGED, "badged", 1, "2024-06-01T00:00:00Z"))
        conn.execute(
            "INSERT INTO users (user_id, username, verified_badge, created_at) "
            "VALUES (?, ?, ?, ?)", (SUSPENDED, "suspended-shop", 1, "2022-01-01T00:00:00Z"))

        # The seller typed the handle *with* an @ into the application form. This one
        # row is the origin of "@@Pilot-8919".
        conn.execute(
            "INSERT INTO marketplace_merchant_applications "
            "(user_id, status, seller_type, business_name, business_description, "
            " pulse_username, email, phone, website, state_region, country, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (SELLER, "submitted", "individual", "Pilot Supply", "NNNNNN",
             "@Pilot-8919", "shop@example.com", "+15550100", "https://pilot.example",
             "New York", "United States", "2024-03-02T00:00:00Z"))
        conn.execute(
            "INSERT INTO marketplace_merchant_applications "
            "(user_id, status, seller_type, business_name, pulse_username, country, created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (APPROVED, "approved", "business", "Approved Shop", "approved-shop",
             "United States", "2023-01-05T00:00:00Z"))
        conn.execute(
            "INSERT INTO marketplace_merchant_applications "
            "(user_id, status, seller_type, business_name, pulse_username, created_at) "
            "VALUES (?,?,?,?,?,?)",
            (BADGED, "submitted", "individual", "Badged Shop", "badged",
             "2024-06-01T00:00:00Z"))
        conn.execute(
            "INSERT INTO marketplace_merchant_applications "
            "(user_id, status, seller_type, business_name, pulse_username, created_at) "
            "VALUES (?,?,?,?,?,?)",
            (SUSPENDED, "approved", "business", "Suspended Shop", "suspended-shop",
             "2022-01-01T00:00:00Z"))

        conn.execute(
            "INSERT INTO verification_requests "
            "(user_id, verification_type, status, decided_at, created_at) VALUES (?,?,?,?,?)",
            (APPROVED, "business", "approved", "2023-02-01T00:00:00Z", "2023-01-20T00:00:00Z"))
        conn.execute(
            "INSERT INTO verification_requests "
            "(user_id, verification_type, status, created_at) VALUES (?,?,?,?)",
            (SUSPENDED, "business", "suspended", "2024-09-01T00:00:00Z"))
        conn.commit()
    finally:
        conn.close()


def setup_module(module=None):
    schema.ensure_schema()
    schema.ensure_schema()  # idempotent second call must not raise
    _fixture_tables()


def _expect(fn, code=None):
    try:
        fn()
    except ProfileError as exc:
        if code is not None:
            assert exc.code == code, f"expected code {code!r}, got {exc.code!r}"
        return exc
    raise AssertionError("expected ProfileError, none raised")


# --------------------------------------------------------------------------- #
# Handle
# --------------------------------------------------------------------------- #

def test_handle_is_normalised_once():
    assert svc.normalize_handle("@@Pilot-8919") == "@Pilot-8919"
    assert svc.normalize_handle("@@@Pilot-8919") == "@Pilot-8919"
    assert svc.normalize_handle("Pilot-8919") == "@Pilot-8919"
    assert svc.normalize_handle("  @Pilot-8919  ") == "@Pilot-8919"
    # Empty stays empty: "@" alone is not a handle, it is a stray glyph on a header.
    assert svc.normalize_handle("") == ""
    assert svc.normalize_handle(None) == ""
    assert svc.normalize_handle("@") == ""

    # And through the real read path, with the application row that caused it.
    owner = svc.owner_profile(SELLER)
    assert owner["handle"] == "@Pilot-8919", owner["handle"]
    assert not owner["handle"].startswith("@@")


def test_handle_availability_is_answered_before_the_save():
    taken = svc.check_handle(SELLER, "approved-shop")
    assert taken["available"] is False and "taken" in taken["reason"].lower()

    mine = svc.check_handle(SELLER, "@Pilot-8919")
    assert mine["available"] is True and mine["is_current"] is True

    free = svc.check_handle(SELLER, "pilot-supply-co")
    assert free["available"] is True and free["is_current"] is False

    for bad in ("ab", "has spaces", "no/slashes", "x" * 41):
        result = svc.check_handle(SELLER, bad)
        assert result["available"] is False, bad

    # "Unavailable" is an answer, not a failure — the editor needs a 200 to render it.
    status, body = api.check_handle(SELLER, "approved-shop")
    assert status == 200 and body["ok"] is True
    assert body["handle"]["available"] is False


# --------------------------------------------------------------------------- #
# Verification — one authoritative state
# --------------------------------------------------------------------------- #

def test_verification_has_one_authoritative_state():
    # A business-track request outranks everything else.
    resolved = svc.resolve_verification(
        business_request={"status": "under_review", "id": 5},
        application_status="approved", verified_badge=1)
    assert resolved["state"] == "under_review"
    assert resolved["source"] == "verification_request"

    # No request: the application speaks, and a *personal* badge cannot promote an
    # unreviewed business to "Approved". This is the contradiction in the screenshot.
    badged = svc.owner_profile(BADGED)
    assert badged["verification"]["state"] != "approved", badged["verification"]
    assert badged["verification"]["source"] != "verified_badge"

    approved = svc.owner_profile(APPROVED)
    assert approved["verification"]["state"] == "approved"
    assert approved["verification"]["source"] == "verification_request"

    # Every state the screen can render is one the vocabulary knows about.
    for user in (SELLER, APPROVED, BADGED, SUSPENDED, FRESH):
        state = svc.owner_profile(user)["verification"]["state"]
        assert state in svc.VERIFICATION_STATES, state

    # The owner view and the buyer view agree. Two derivations is how they diverged.
    for user in (SELLER, APPROVED, BADGED):
        owner = svc.owner_profile(user)
        public = svc.public_profile(user)
        assert public["verified"] == (owner["verification"]["state"] == "approved")


def test_review_narrows_to_identity_fields_only():
    locks = svc.verification_locks("approved")
    assert locks["blocked"] == []
    assert set(locks["requires_review"]) == set(svc.IDENTITY_SENSITIVE_FIELDS)
    assert "You can update this field" in locks["explainer"]

    # Enforcement blocks identity, and *only* identity.
    enforced = svc.verification_locks("suspended")
    assert set(enforced["blocked"]) == set(svc.IDENTITY_SENSITIVE_FIELDS)
    assert "Everything else stays editable" in enforced["explainer"]

    # A submitted application freezes the application, never the living profile.
    submitted = svc.owner_profile(SELLER)
    assert submitted["locks"]["blocked"] == []
    result = svc.update_profile(SELLER, {"tagline": "Parts, fast."})
    assert result["saved"].get("tagline") == "Parts, fast."
    assert result["rejected"] == {}


def test_identity_change_queues_review_without_blocking_the_save():
    result = svc.update_profile(APPROVED, {"business_name": "Approved Shop Ltd",
                                           "tagline": "Since 2023"})
    assert result["saved"]["business_name"] == "Approved Shop Ltd"
    assert result["queued_for_review"] == ["business_name"]
    assert result["saved"]["tagline"] == "Since 2023"
    assert result["rejected"] == {}
    # The change is written, not held in limbo — the brief's "should not simply be locked".
    assert svc.owner_profile(APPROVED)["business_name"] == "Approved Shop Ltd"

    # And it is auditable, because this is exactly the change a reviewer will query.
    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT field, before_value, after_value, requires_review "
            "FROM business_os_seller_profile_audit WHERE user_id=? AND field=?",
            (APPROVED, "business_name")).fetchall()
    finally:
        conn.close()
    assert rows, "identity change was not audited"
    entry = dict(rows[-1])
    assert entry["after_value"] == "Approved Shop Ltd"
    assert int(entry["requires_review"]) == 1

    blocked = svc.update_profile(SUSPENDED, {"business_name": "Renamed",
                                             "tagline": "still editable"})
    assert "business_name" in blocked["rejected"]
    assert blocked["saved"].get("tagline") == "still editable"


# --------------------------------------------------------------------------- #
# Partial saves
# --------------------------------------------------------------------------- #

def test_partial_save_keeps_the_valid_fields():
    result = svc.update_profile(SELLER, {
        "about": "We stock replacement parts for light aircraft.",
        "what_you_sell": "Avionics, fasteners, tooling",
        "shipping_summary": "Ships in 2 business days",
        "return_summary": "30-day returns on unopened parts",
        "support_email": "not-an-email",           # the one bad field
    })
    assert set(result["saved"]) == {"about", "what_you_sell", "shipping_summary",
                                    "return_summary"}
    assert "support_email" in result["rejected"]
    assert result["profile"]["about"].startswith("We stock")

    status, body = api.update_profile(SELLER, {"tagline": "Aircraft parts",
                                               "user_id": 1, "verification": "approved"})
    # Partial success is a 200; server-authoritative keys are dropped, not 400'd.
    assert status == 200 and body["ok"] is True
    assert body["saved"]["tagline"] == "Aircraft parts"
    assert set(body["ignored"]) == {"user_id", "verification"}
    assert body["profile"]["verification"]["state"] in svc.VERIFICATION_STATES


def test_writable_fields_cannot_reach_server_owned_state():
    for guarded in ("user_id", "verification", "locks", "sync", "completion",
                    "published_at", "updated_at", "seller_type", "handle"):
        assert guarded not in svc.WRITABLE_FIELDS, guarded


def test_seller_type_is_not_a_business_category():
    owner = svc.owner_profile(SELLER)
    assert owner["seller_type"] == "individual"
    assert owner["business_category"] == ""      # never seeded from seller_type
    assert "individual" not in svc.BUSINESS_CATEGORIES

    svc.update_profile(SELLER, {"business_category": "retail"})
    owner = svc.owner_profile(SELLER)
    assert owner["business_category"] == "retail"
    assert owner["business_category_label"]
    assert owner["seller_type"] == "individual"  # the two coexist, separately

    rejected = svc.update_profile(SELLER, {"business_category": "individual"})
    assert "business_category" in rejected["rejected"]

    # The buyer sees the category, never the reviewer's classification.
    public = svc.public_profile(SELLER)
    assert public["business_category"] == "retail"
    assert "seller_type" not in public


# --------------------------------------------------------------------------- #
# Completeness
# --------------------------------------------------------------------------- #

def test_completeness_itemises_what_is_missing():
    completion = svc.owner_profile(FRESH)["completion"]
    assert completion["percent"] == 0
    assert len(completion["missing"]) == len(svc.COMPLETION_ITEMS)
    assert completion["next_label"], "a completeness card with no next step is a scold"

    completion = svc.owner_profile(SELLER)["completion"]
    labels = {item["label"] for item in completion["missing"]}
    assert labels, "the seller cannot be complete at this point in the test"
    # Every missing entry is a named, actionable thing — never a bare percentage.
    for item in completion["missing"]:
        assert item["label"] and item["key"]

    # The headline and the checklist are derived from one list, so they cannot drift.
    total = len(completion["completed"]) + len(completion["missing"])
    assert total == completion["total"] == len(svc.COMPLETION_ITEMS)
    assert completion["percent"] == round(len(completion["completed"]) * 100 / total)


# --------------------------------------------------------------------------- #
# Hours
# --------------------------------------------------------------------------- #

def test_hours_distinguish_unset_from_closed():
    fresh = svc.owner_profile(FRESH)
    assert len(fresh["hours"]) == 7
    assert {day["state"] for day in fresh["hours"]} == {"unset"}
    assert fresh["hours_mode"] == "unset"

    svc.set_hours(SELLER, "weekly", [
        {"weekday": "mon", "opens": "09:00", "closes": "17:30"},
        {"weekday": "sun", "closed": True},
    ])
    hours = {day["weekday"]: day for day in svc.owner_profile(SELLER)["hours"]}
    assert hours["mon"]["state"] == "open" and hours["mon"]["closes"] == "17:30"
    assert hours["sun"]["state"] == "closed"
    assert hours["tue"]["state"] == "unset"     # not stored is not "shut"

    _expect(lambda: svc.set_hours(SELLER, "weekly",
                                  [{"weekday": "mon", "opens": "18:00", "closes": "02:00"}]),
            code="invalid_range")
    _expect(lambda: svc.set_hours(SELLER, "weekly",
                                  [{"weekday": "mon", "opens": "9am", "closes": "5pm"}]),
            code="invalid_time")
    _expect(lambda: svc.set_hours(SELLER, "sometimes"), code="invalid_hours_mode")

    svc.set_hours_override(SELLER, "2026-12-25", closed=True, label="Christmas Day")
    overrides = svc.owner_profile(SELLER)["hours_overrides"]
    assert any(o["date"] == "2026-12-25" and o["closed"] for o in overrides)
    _expect(lambda: svc.set_hours_override(SELLER, "25/12/2026"), code="invalid_date")

    # Restore the weekly pattern the later tests read.
    svc.set_hours(SELLER, "weekly", [
        {"weekday": day, "opens": "09:00", "closes": "17:30"}
        for day in ("mon", "tue", "wed", "thu", "fri")
    ] + [{"weekday": "sat", "closed": True}, {"weekday": "sun", "closed": True}])


# --------------------------------------------------------------------------- #
# Links, addresses, contact visibility
# --------------------------------------------------------------------------- #

def test_links_validate_and_clear():
    svc.set_link(SELLER, "instagram", "https://instagram.com/pilotsupply")
    kinds = {link["kind"]: link["url"] for link in svc.owner_profile(SELLER)["links"]}
    assert kinds["instagram"].endswith("pilotsupply")

    _expect(lambda: svc.set_link(SELLER, "instagram", "instagram.com/x"), code="invalid_url")
    _expect(lambda: svc.set_link(SELLER, "myspace", "https://x.example"),
            code="invalid_link_kind")

    svc.set_link(SELLER, "instagram", "")
    assert "instagram" not in {link["kind"] for link in svc.owner_profile(SELLER)["links"]}


def test_addresses_are_operational_only():
    assert "legal" not in svc.ADDRESS_KINDS, (
        "a registered address is verification evidence, not profile copy")
    _expect(lambda: svc.set_address(SELLER, "legal", {"line1": "1 Registry Way"}),
            code="invalid_address_kind")

    svc.set_address(SELLER, "pickup", {"line1": "12 Hangar Row", "city": "New York",
                                       "country": "United States"})
    kinds = {a["kind"] for a in svc.owner_profile(SELLER)["addresses"]}
    assert "pickup" in kinds


def test_contact_defaults_to_private_and_visibility_is_honoured():
    fresh = svc.owner_profile(FRESH)["contact"]
    assert fresh["email_visibility"] == "private"
    assert fresh["phone_visibility"] == "private", "a phone must never default to public"

    svc.update_profile(SELLER, {"support_email": "shop@example.com",
                                "support_phone": "+15550100"})
    public = svc.public_profile(SELLER)
    assert "email" not in public["contact"] and "phone" not in public["contact"]

    svc.update_profile(SELLER, {"support_email_visibility": "after_purchase"})
    assert "email" not in svc.public_profile(SELLER)["contact"]
    assert "email" in svc.public_profile(SELLER, viewer_has_purchased=True)["contact"]

    svc.update_profile(SELLER, {"support_email_visibility": "public"})
    assert svc.public_profile(SELLER)["contact"]["email"] == "shop@example.com"
    # Raising the email must not have raised the phone with it.
    assert "phone" not in svc.public_profile(SELLER)["contact"]

    # An unknown visibility is rejected, never silently coerced. Coercing "everyone"
    # down to "private" would be safe and dishonest: the seller would believe they
    # had published a number they had not.
    bad = svc.update_profile(SELLER, {"support_phone_visibility": "everyone"})
    assert "support_phone_visibility" in bad["rejected"]
    assert svc.owner_profile(SELLER)["contact"]["phone_visibility"] == "private"


# --------------------------------------------------------------------------- #
# The buyer view
# --------------------------------------------------------------------------- #

def test_public_profile_publishes_nothing_private():
    svc.update_profile(SELLER, {"legal_name": "Pilot Supply Holdings LLC"})
    public = svc.public_profile(SELLER)

    for forbidden in svc.NEVER_PUBLIC:
        assert forbidden not in public, f"{forbidden} leaked into the buyer view"

    # An allowlist, not a redaction: every key is one somebody chose to publish.
    assert set(public).issubset(set(svc.PUBLIC_FIELDS)), set(public) - set(svc.PUBLIC_FIELDS)

    blob = repr(public)
    assert "Pilot Supply Holdings LLC" not in blob
    assert "Hangar Row" not in blob          # the pickup address
    assert "+15550100" not in blob           # phone is still private

    # Coarse location only — the town, never the doorstep.
    assert public["location"] == "New York, United States" or public["location"]
    assert "12 Hangar Row" not in public["location"]


def test_preview_is_the_strictest_view_not_the_flattering_one():
    svc.update_profile(SELLER, {"support_phone_visibility": "after_purchase"})
    status, body = api.preview_profile(SELLER)
    assert status == 200 and body["ok"] is True
    assert body["preview"]["active"] is True
    assert body["preview"]["subtitle"].startswith("This is how your public")
    assert "phone" not in body["profile"]["contact"], (
        "the preview must not show the owner a view most buyers never get")
    for forbidden in svc.NEVER_PUBLIC:
        assert forbidden not in body["profile"]
    # The owner-unsafe actions are named so the client can disable them.
    assert {"message", "follow", "buy"}.issubset(set(body["preview"]["simulated_actions"]))


def test_public_route_is_read_only_and_identical_for_the_owner():
    owner_view = api.get_public_profile(SELLER, viewer_user_id=SELLER)
    stranger_view = api.get_public_profile(SELLER, viewer_user_id=9999)
    assert owner_view[0] == stranger_view[0] == 200
    assert owner_view[1]["profile"] == stranger_view[1]["profile"]
    assert owner_view[1]["is_self"] is True and stranger_view[1]["is_self"] is False

    missing = api.get_public_profile("not-a-number")
    assert missing[0] == 404 and missing[1]["ok"] is False


# --------------------------------------------------------------------------- #
# Live sync
# --------------------------------------------------------------------------- #

def test_sync_never_claims_unpublished_edits_are_live():
    assert set(svc.SERVER_SYNC_STATES) == {"synced", "changes_pending", "review_required"}

    assert svc.owner_profile(FRESH)["sync"]["state"] == "changes_pending"

    svc.publish(SELLER)
    assert svc.owner_profile(SELLER)["sync"]["state"] == "synced"

    svc.update_profile(SELLER, {"tagline": "Edited after publishing"})
    after = svc.owner_profile(SELLER)["sync"]
    assert after["state"] == "changes_pending", (
        "an edit made after publishing must not report as live")

    assert svc.owner_profile(SUSPENDED)["sync"]["state"] == "review_required"

    status, body = api.sync_status(SELLER)
    assert status == 200
    assert body["sync"]["state"] == "changes_pending"
    assert body["published_at"] and body["updated_at"]
    assert isinstance(body["review_protected_fields"], list)


# --------------------------------------------------------------------------- #
# Controller shape
# --------------------------------------------------------------------------- #

def test_every_handler_returns_status_and_ok():
    calls = [
        api.get_profile(SELLER),
        api.update_profile(SELLER, {"tagline": "x"}),
        api.set_hours(SELLER, {"mode": "by_appointment"}),
        api.set_hours_override(SELLER, {"date": "2026-01-01", "closed": True}),
        api.set_link(SELLER, {"kind": "website", "url": "https://pilot.example"}),
        api.set_address(SELLER, {"kind": "shipping_origin", "city": "Newark"}),
        api.check_handle(SELLER, "pilot-supply"),
        api.publish(SELLER),
        api.sync_status(SELLER),
        api.get_public_profile(SELLER),
        api.preview_profile(SELLER),
        api.vocabularies(),
    ]
    for status, body in calls:
        assert isinstance(status, int) and isinstance(body, dict)
        assert isinstance(body.get("ok"), bool)
        if not body["ok"]:
            assert body.get("error") and body.get("code")

    # No identity, no write — and the message says what to do about it.
    for status, body in (api.get_profile(None), api.update_profile("", {"tagline": "x"}),
                         api.publish(0)):
        assert status == 401 and body["code"] == "unauthenticated"

    # Bad vocabulary is a curated 400, never a leaked exception string.
    status, body = api.set_link(SELLER, {"kind": "myspace", "url": "https://x.example"})
    assert status == 400 and body["code"] == "invalid_link_kind"
    assert "Traceback" not in body["error"]


def test_vocabularies_serve_the_pickers_without_leaking_seller_type():
    status, body = api.vocabularies()
    vocab = body["vocabularies"]
    assert status == 200
    assert "seller_type" not in vocab
    assert {"value": "public", "label": "Visible to all buyers"} in vocab["contact_visibility"]
    assert len(vocab["weekdays"]) == 7
    assert set(vocab["verification_states"]) == set(svc.VERIFICATION_STATES)
    assert "business_name" in vocab["writable_fields"]
    for entry in vocab["business_categories"]:
        assert entry["value"] and entry["label"]
        assert entry["value"] != "individual"


def _run_standalone():
    setup_module()
    tests = [
        test_handle_is_normalised_once,
        test_handle_availability_is_answered_before_the_save,
        test_verification_has_one_authoritative_state,
        test_review_narrows_to_identity_fields_only,
        test_identity_change_queues_review_without_blocking_the_save,
        test_partial_save_keeps_the_valid_fields,
        test_writable_fields_cannot_reach_server_owned_state,
        test_seller_type_is_not_a_business_category,
        test_completeness_itemises_what_is_missing,
        test_hours_distinguish_unset_from_closed,
        test_links_validate_and_clear,
        test_addresses_are_operational_only,
        test_contact_defaults_to_private_and_visibility_is_honoured,
        test_public_profile_publishes_nothing_private,
        test_preview_is_the_strictest_view_not_the_flattering_one,
        test_public_route_is_read_only_and_identical_for_the_owner,
        test_sync_never_claims_unpublished_edits_are_live,
        test_every_handler_returns_status_and_ok,
        test_vocabularies_serve_the_pickers_without_leaking_seller_type,
    ]
    passed = 0
    failed = []
    for test in tests:
        # Every test runs, whatever the ones before it did. Aborting on the first
        # exception would let a single failure hide the eighteen verdicts behind it,
        # and under revert validation — where the whole point is to see *which*
        # tests notice a reintroduced defect — that hiding makes the exercise
        # useless.
        try:
            test()
        except Exception as exc:  # noqa: BLE001 - the message is the output
            failed.append(test.__name__)
            print(f"FAIL  {test.__name__}: {type(exc).__name__}: {exc}")
            continue
        print(f"PASS  {test.__name__}")
        passed += 1
    print(f"\n{passed}/{len(tests)} tests passed")
    if failed:
        print("failed: " + ", ".join(failed))
    return not failed


if __name__ == "__main__":
    raise SystemExit(0 if _run_standalone() else 1)
