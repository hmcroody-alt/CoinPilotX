"""The two row-level premium readers must agree about what time it is.

``premium_visibility_engine.is_premium_user`` (placement/prestige) and
``premium_identity_engine.has_active_premium`` (the badge) are handed the SAME
user row by the same feed and profile queries. The identity reader has always
consulted the recorded period end; the visibility reader answered from the
status WORD alone.

That asymmetry is not cosmetic. It means:

* a row left at ``premium_status='active'`` by a provider webhook that never
  arrived reads as Premium forever on one surface and correctly lapsed on the
  other, and
* any writer of a TIME-BOXED grant that mirrors onto these columns gets a
  permanent premium flag on the visibility surface, whatever expiry it recorded.

The second point is the one that bites: a bounded grant cannot be expressed in
these columns at all while one reader refuses to look at the clock.

Both readers now share ``premium_identity_engine.row_period_ended``.

What must NOT change: unbounded grants. ``lifetime_premium`` and
``premium_glow_manual_grant`` are deliberate permanent marks, and the status
words 'founder'/'lifetime' declare in the word itself that there is no term.
A stale expiry column sitting next to one of those is meaningless and must not
revoke anything.

    python -m pytest tests/business_os/test_premium_visibility_clock.py
"""

import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from services import premium_visibility_engine as pve  # noqa: E402
from services import premium_identity_engine as pie  # noqa: E402


def _iso(delta):
    return (datetime.now(timezone.utc) + delta).isoformat()


PAST = _iso(timedelta(days=-3))
FUTURE = _iso(timedelta(days=30))


# --- the term-bounded branches now honour the clock -------------------------

def test_active_status_with_open_term_is_premium():
    assert pve.is_premium_user(
        {"user_id": 1, "premium_status": "active", "premium_expires_at": FUTURE}
    ) is True


def test_active_status_with_ended_term_is_not_premium():
    """The regression this file exists for: a time-boxed grant mirrored onto
    the identity columns must stop reading as Premium when its term closes."""
    assert pve.is_premium_user(
        {"user_id": 1, "premium_status": "active", "premium_expires_at": PAST}
    ) is False


def test_trial_status_with_ended_term_is_not_premium():
    assert pve.is_premium_user(
        {"user_id": 1, "premium_status": "trial", "premium_expires_at": PAST}
    ) is False


def test_subscription_plan_branch_with_ended_term_is_not_premium():
    assert pve.is_premium_user({
        "user_id": 1, "subscription_plan": "premium",
        "subscription_status": "active", "subscription_expires_at": PAST,
    }) is False


def test_subscription_plan_branch_with_open_term_is_premium():
    assert pve.is_premium_user({
        "user_id": 1, "subscription_plan": "premium",
        "subscription_status": "active", "subscription_expires_at": FUTURE,
    }) is True


# --- no recorded end means no evidence of expiry ----------------------------
# Every pre-existing caller passes rows with no expiry column at all. They must
# be answered exactly as before, or this fix silently demotes real members.

def test_active_status_with_no_recorded_end_is_unchanged():
    assert pve.is_premium_user({"user_id": 1, "premium_status": "active"}) is True


def test_unparseable_end_does_not_revoke():
    assert pve.is_premium_user(
        {"user_id": 1, "premium_status": "active", "premium_expires_at": "soon"}
    ) is True


# --- unbounded grants are untouched by the clock ----------------------------

def test_lifetime_premium_survives_a_stale_expiry():
    assert pve.is_premium_user({
        "user_id": 1, "lifetime_premium": 1,
        "premium_status": "inactive", "premium_expires_at": PAST,
    }) is True


def test_manual_glow_grant_survives_a_stale_expiry():
    assert pve.is_premium_user({
        "user_id": 1, "premium_glow_manual_grant": 1,
        "premium_status": "inactive", "premium_expires_at": PAST,
    }) is True


def test_founder_status_survives_a_stale_expiry():
    assert pve.is_premium_user(
        {"user_id": 1, "premium_status": "founder", "premium_expires_at": PAST}
    ) is True


def test_lifetime_status_survives_a_stale_expiry():
    assert pve.is_premium_user(
        {"user_id": 1, "premium_status": "lifetime", "premium_expires_at": PAST}
    ) is True


# --- non-premium stays non-premium ------------------------------------------

def test_inactive_is_not_premium():
    assert pve.is_premium_user({"user_id": 1, "premium_status": "inactive"}) is False


def test_empty_row_is_not_premium():
    assert pve.is_premium_user({}) is False
    assert pve.is_premium_user(None) is False


# --- the actual point: the two readers now agree ----------------------------

def test_both_row_readers_agree_across_the_boundary():
    """The contract. Same row, both readers, on each side of the term end."""
    for expiry, expected in ((FUTURE, True), (PAST, False)):
        row = {"user_id": 1, "premium_status": "active",
               "premium_expires_at": expiry, "plan": "premium", "is_pro": 1}
        assert pve.is_premium_user(row) is expected
        assert pie.has_active_premium(row) is expected
        assert bool(pie.identity_mark(row)) is expected
