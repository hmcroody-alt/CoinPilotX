"""Premium unlocks the Blue Check *application*. It does not buy the badge.

That sentence is the whole product decision, and every test here defends one
half of it:

  * the application is genuinely gated — a non-member cannot submit, and the
    refusal happens in the service, not in the UI;
  * the badge is genuinely NOT gated — approving is a reviewer's act, an
    approved badge outlives the membership that unlocked the form, and a
    successful submission on its own changes nothing about verified status.

The second half matters more than the first. A gate that leaks lets someone
apply for free; a badge coupled to billing makes verification purchasable,
which is the failure this feature was explicitly designed to avoid.

Function-level tests against fresh in-memory SQLite, in the style of
``tests/test_app_review_qa_visibility_and_appeals.py``. Nothing touches
coinpilotx.db and bot.py is never booted.
"""

import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services import schema_guard  # noqa: E402
from services import dashboard_account_command_center as center  # noqa: E402
from services.business_os.entitlements import facade as ent_facade  # noqa: E402
from services.business_os.entitlements import premium as ent_premium  # noqa: E402
from services.business_os.entitlements import premium_api  # noqa: E402
from services.business_os.entitlements import readiness  # noqa: E402
from services.business_os.entitlements import schema as ent_schema  # noqa: E402

CAPABILITY = center.BLUE_CHECK_APPLY_CAPABILITY
MEMBER = 5001
FREELOADER = 5002


def _fresh_conn():
    schema_guard.reset_all()
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            email TEXT,
            display_name TEXT,
            account_status TEXT DEFAULT 'active',
            access_enabled INTEGER DEFAULT 1,
            verified_badge INTEGER DEFAULT 0,
            verification_status TEXT,
            updated_at TEXT
        )
        """
    )
    conn.execute("INSERT INTO users (user_id, username) VALUES (?, 'member')", (MEMBER,))
    conn.execute("INSERT INTO users (user_id, username) VALUES (?, 'freeloader')", (FREELOADER,))
    conn.commit()
    return conn


@pytest.fixture
def conn():
    connection = _fresh_conn()
    try:
        yield connection
    finally:
        connection.close()


@pytest.fixture
def entitled(monkeypatch):
    """Control the canonical entitlement answer.

    The gate's contract is "ask the facade and obey it", so the facade is the
    seam. Whether the facade itself resolves correctly is the subject of
    ``tests/business_os/test_premium_canonical.py`` and of the registry tests at
    the bottom of this file; duplicating its internals here would test the mock.
    """
    holders: set[int] = set()

    def _check(subject_id, key, **kwargs):
        if key != CAPABILITY:
            raise AssertionError(f"gate consulted an unexpected capability: {key}")
        return int(subject_id) in holders

    monkeypatch.setattr(ent_facade, "check", _check)
    return holders


def _row(conn, request_id):
    cur = conn.cursor()
    cur.execute("SELECT * FROM verification_requests WHERE id=?", (int(request_id),))
    return cur.fetchone()


def _count(conn, user_id, kind="blue_check"):
    cur = conn.cursor()
    cur.execute(
        "SELECT COUNT(*) FROM verification_requests WHERE user_id=? AND verification_type=?",
        (int(user_id), kind),
    )
    return cur.fetchone()[0]


# --- the gate ---------------------------------------------------------------
def test_non_member_cannot_submit_a_blue_check_application(conn, entitled):
    with pytest.raises(PermissionError) as refusal:
        center.submit_verification_request(conn, FREELOADER, "blue_check", {})
    # The refusal names the membership, so the route can offer it rather than
    # rendering a bare failure.
    assert "Premium" in str(refusal.value)
    assert _count(conn, FREELOADER) == 0


def test_refusal_copy_never_promises_the_badge(conn):
    """The message a declined applicant reads is the riskiest copy in the flow.

    It is the moment someone is most likely to hear "pay and you're verified",
    so it must say the opposite in the same breath.
    """
    message = center.BLUE_CHECK_PREMIUM_REQUIRED.lower()
    assert "reviewer still decides" in message
    for lie in ("get verified", "guaranteed", "instant", "faster review", "priority"):
        assert lie not in message


def test_member_can_submit_a_blue_check_application(conn, entitled):
    entitled.add(MEMBER)
    result = center.submit_verification_request(conn, MEMBER, "blue_check", {})
    assert result["ok"] is True
    assert result["request_id"] > 0
    assert result["status"] == "submitted"
    assert _row(conn, result["request_id"])["verification_type"] == "blue_check"


def test_submitting_does_not_verify_anybody(conn, entitled):
    """The purchase-to-badge shortcut, tested directly.

    A member pays, applies, and succeeds — and is still not verified. Only a
    reviewer's decision may set that column.
    """
    entitled.add(MEMBER)
    center.submit_verification_request(conn, MEMBER, "blue_check", {})
    cur = conn.cursor()
    cur.execute("SELECT verified_badge FROM users WHERE user_id=?", (MEMBER,))
    assert cur.fetchone()["verified_badge"] == 0


def test_ungated_tracks_are_untouched_by_the_premium_gate(conn, entitled):
    """Identity verification is account safety, not a product tier.

    Gating the wrong track would put basic account security behind a paywall,
    so the gate is asserted to be narrow rather than merely present.
    """
    result = center.submit_verification_request(conn, FREELOADER, "identity", {})
    assert result["ok"] is True
    assert result["status"] == "submitted"


def test_only_blue_check_is_premium_gated():
    assert center.PREMIUM_GATED_VERIFICATION_TYPES == {"blue_check"}


def test_an_account_hold_suspends_the_application(monkeypatch):
    """A suspended account must not be able to apply, membership or not.

    The gate passes the hold context to the facade rather than deciding on its
    own, so this asserts the context actually travels.
    """
    seen = {}

    def _check(subject_id, key, **kwargs):
        seen.update(kwargs.get("context") or {})
        return True

    monkeypatch.setattr(ent_facade, "check", _check)
    center.blue_check_apply_allowed_for(MEMBER, {"account_status": "suspended", "access_enabled": 0})
    assert seen == {"account_status": "suspended", "access_enabled": 0}


def test_a_broken_entitlement_read_denies_rather_than_grants(monkeypatch):
    def _explode(*_args, **_kwargs):
        raise RuntimeError("entitlement backend down")

    monkeypatch.setattr(ent_facade, "check", _explode)
    assert center.blue_check_apply_allowed_for(MEMBER, {}) is False


# --- one application at a time ----------------------------------------------
def test_a_second_application_returns_the_first_instead_of_duplicating(conn, entitled):
    entitled.add(MEMBER)
    first = center.submit_verification_request(conn, MEMBER, "blue_check", {})
    second = center.submit_verification_request(conn, MEMBER, "blue_check", {})
    assert second["duplicate"] is True
    assert second["request_id"] == first["request_id"]
    assert _count(conn, MEMBER) == 1


def test_a_decided_application_does_not_block_a_new_one(conn, entitled):
    """Refused once is not refused forever.

    'Open' means still with reviewers. A rejected application has been decided,
    so it must not wedge the track shut.
    """
    entitled.add(MEMBER)
    first = center.submit_verification_request(conn, MEMBER, "blue_check", {})
    conn.execute("UPDATE verification_requests SET status='rejected' WHERE id=?", (first["request_id"],))
    conn.commit()
    second = center.submit_verification_request(conn, MEMBER, "blue_check", {})
    assert second.get("duplicate") is not True
    assert second["request_id"] != first["request_id"]


def test_open_statuses_cover_every_state_still_with_reviewers(conn, entitled):
    entitled.add(MEMBER)
    for status in center.OPEN_VERIFICATION_STATUSES:
        first = center.submit_verification_request(conn, MEMBER, "blue_check", {})
        conn.execute("UPDATE verification_requests SET status=? WHERE id=?", (status, first["request_id"]))
        conn.commit()
        again = center.submit_verification_request(conn, MEMBER, "blue_check", {})
        assert again["duplicate"] is True, f"a {status} request must not accept a second application"
        # Clear the track before the next status. (Not before the first submit:
        # the table is created by ``ensure_schema`` inside it.)
        conn.execute("DELETE FROM verification_requests")
        conn.commit()


# --- what happens when the membership ends ----------------------------------
def test_a_lapsed_membership_does_not_cancel_a_live_application(conn, entitled):
    """Billing must never reach into an in-flight review.

    Someone who applied in good faith and then let the membership lapse has a
    case sitting with a reviewer. Deleting or cancelling it would make the
    review outcome a function of payment status — the exact coupling this
    feature refuses.
    """
    entitled.add(MEMBER)
    submitted = center.submit_verification_request(conn, MEMBER, "blue_check", {})
    conn.execute("UPDATE verification_requests SET status='in_review' WHERE id=?", (submitted["request_id"],))
    conn.commit()

    entitled.discard(MEMBER)  # membership ends mid-review

    row = _row(conn, submitted["request_id"])
    assert row is not None, "the application was destroyed when the membership ended"
    assert row["status"] == "in_review"


def test_a_lapsed_membership_blocks_a_new_application(conn, entitled):
    entitled.add(MEMBER)
    first = center.submit_verification_request(conn, MEMBER, "blue_check", {})
    conn.execute("UPDATE verification_requests SET status='rejected' WHERE id=?", (first["request_id"],))
    conn.commit()
    entitled.discard(MEMBER)
    with pytest.raises(PermissionError):
        center.submit_verification_request(conn, MEMBER, "blue_check", {})


def test_an_approved_badge_outlives_the_membership(conn, entitled):
    """The badge is earned, not rented.

    Verification says a reviewer confirmed who someone is. That fact does not
    expire because a subscription did, so nothing in the billing path may clear
    it.
    """
    entitled.add(MEMBER)
    submitted = center.submit_verification_request(conn, MEMBER, "blue_check", {})
    center.admin_decide_verification(conn, submitted["request_id"], 9001, "approved", "Evidence checked.")
    entitled.discard(MEMBER)

    cur = conn.cursor()
    cur.execute("SELECT verified_badge FROM users WHERE user_id=?", (MEMBER,))
    assert cur.fetchone()["verified_badge"] == 1
    assert center.blue_check_apply_allowed_for(MEMBER, {}) is False  # and cannot re-apply


# --- the registry: what we are allowed to say --------------------------------
def test_the_capability_is_registered_as_a_premium_capability():
    assert CAPABILITY in ent_premium.PREMIUM_CAPABILITIES


def test_the_capability_is_sellable_and_names_the_gate_that_enforces_it():
    feature = readiness.get(CAPABILITY)
    assert feature is not None
    assert feature.sellable, "an advertised benefit must be enforced by real code"
    assert feature.enforced_by.endswith("submit_verification_request")


def test_the_advertised_label_sells_the_application_not_the_outcome():
    label = readiness.get(CAPABILITY).label.lower()
    assert "application" in label
    for lie in ("get verified", "guaranteed", "instant", "automatic"):
        assert lie not in label


def test_every_premium_plan_grants_the_capability():
    """A benefit nobody's plan grants is a benefit nobody receives."""
    granting = {plan for plan, key, _limit, _period in ent_schema._SEED_CATALOG if key == CAPABILITY}
    premium_plans = {plan for plan, key, _limit, _period in ent_schema._SEED_CATALOG
                     if key == ent_premium.PREMIUM_ACCESS}
    assert premium_plans, "no plan grants premium access at all — the fixture is wrong"
    assert premium_plans <= granting, f"plans granting Premium but not Blue Check access: {premium_plans - granting}"


def test_the_capability_has_a_legacy_reader_so_it_survives_flag_modes():
    """The trap that would have silently denied every paying member.

    ``facade.check`` defaults to mode ``off`` when BUSINESS_OS_ENTITLEMENTS is
    unset, and in that mode the answer is the *legacy* opinion. A key absent
    from ``_LEGACY_READERS`` has no legacy opinion, which collapses to False —
    so without this mapping the feature would work in production (mode
    ``canonical``) and deny everyone the instant an operator pulled the flag,
    as well as in every local and test run.
    """
    assert CAPABILITY in ent_facade._LEGACY_READERS


def test_premium_positioning_does_not_claim_neutrality_it_no_longer_has():
    """The disclaimer has to stay honest in both directions.

    It must still deny that Premium *is* verification, and it must no longer
    claim Premium has no bearing on eligibility — because it now unlocks the
    application. What it must never concede is influence over the decision.
    """
    copy = premium_api.NOT_VERIFICATION.lower()
    assert "not identity verification" in copy
    assert "does not affect verification eligibility" not in copy
    assert "reviewer decides" in copy
    assert "does not make approval more likely" in copy
