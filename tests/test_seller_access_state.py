"""The seller access gate: one verdict, fails closed, two axes kept apart.

These tests drive the service through a real sqlite cursor rather than a mock,
because the thing most likely to break here is the SQL — an absent table, a
column that exists on one instance and not another, a status written years ago
under a legacy name. A mocked cursor would agree with whatever I wrote and
prove nothing.
"""

from __future__ import annotations

import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services import seller_access_state as sas  # noqa: E402


class _Cur:
    """A cursor that speaks ``?`` and returns dict-like rows."""

    def __init__(self, conn):
        self._cur = conn.cursor()

    def execute(self, sql, params=()):
        self._cur.execute(sql, params)
        return self

    def fetchone(self):
        row = self._cur.fetchone()
        return dict(row) if row is not None else None


@pytest.fixture()
def cur():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute(
        "CREATE TABLE marketplace_sellers (id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "user_id INTEGER UNIQUE, display_name TEXT, business_name TEXT, "
        "seller_type TEXT, verification_status TEXT, status TEXT DEFAULT 'pending')"
    )
    c.execute(
        "CREATE TABLE marketplace_merchant_applications (id INTEGER PRIMARY KEY "
        "AUTOINCREMENT, user_id INTEGER, display_name TEXT, business_name TEXT, "
        "status TEXT DEFAULT 'draft')"
    )
    c.execute(
        "CREATE TABLE seller_payout_accounts (id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "user_id INTEGER, connected_account_id TEXT, provider_account_id TEXT, "
        "onboarding_status TEXT DEFAULT 'not_started', payouts_enabled INTEGER DEFAULT 0, "
        "charges_enabled INTEGER DEFAULT 0, missing_requirements_json TEXT, "
        "requirements_json TEXT)"
    )
    conn.commit()
    yield _Cur(conn)
    conn.close()


@pytest.fixture(autouse=True)
def _card_rail_on(monkeypatch):
    """Default the platform flag ON so these tests exercise the seller axis.

    The flag's own effect gets its own test below; leaving it off here would
    make every card assertion pass for the wrong reason.
    """
    monkeypatch.setenv("MARKETPLACE_CARD_PAYMENTS_ENABLED", "true")


def _seller(cur, user_id, status, **kw):
    cur.execute(
        "INSERT INTO marketplace_sellers (user_id, status, display_name, business_name) "
        "VALUES (?,?,?,?)",
        (user_id, status, kw.get("display_name"), kw.get("business_name")),
    )


def _application(cur, user_id, status):
    cur.execute(
        "INSERT INTO marketplace_merchant_applications (user_id, status) VALUES (?,?)",
        (user_id, status),
    )


# --------------------------------------------------------------------------- #
# Access state
# --------------------------------------------------------------------------- #


def test_a_user_with_no_rows_at_all_gets_no_application(cur):
    state = sas.get_seller_access_state(cur, 1)
    assert state["seller_application_status"] == sas.NO_APPLICATION
    assert state["seller_approved"] is False
    assert state["store_access"] is False
    assert state["marketplace_selling_access"] is False


def test_an_anonymous_caller_is_refused_without_touching_the_database(cur):
    assert sas.get_seller_access_state(cur, None)["store_access"] is False
    assert sas.get_seller_access_state(cur, 0)["store_access"] is False


@pytest.mark.parametrize(
    "lifecycle,expected",
    [
        ("draft", sas.DRAFT),
        ("submitted", sas.SUBMITTED),
        ("under_review", sas.UNDER_REVIEW),
        ("resubmitted", sas.UNDER_REVIEW),
        ("information_requested", sas.MORE_INFORMATION_REQUIRED),
        ("rejected", sas.DECLINED),
        ("withdrawn", sas.NO_APPLICATION),
        ("expired", sas.NO_APPLICATION),
    ],
)
def test_each_application_status_routes_to_its_screen(cur, lifecycle, expected):
    _application(cur, 7, lifecycle)
    state = sas.get_seller_access_state(cur, 7)
    assert state["seller_application_status"] == expected
    # None of these are approved, so none of them unlock a seller surface.
    assert state["store_access"] is False
    assert state["marketplace_selling_access"] is False


def test_an_approved_seller_unlocks_store_and_selling_together(cur):
    _seller(cur, 3, "approved", display_name="M&W Store")
    _application(cur, 3, "approved")
    state = sas.get_seller_access_state(cur, 3)
    assert state["seller_application_status"] == sas.APPROVED
    assert state["seller_approved"] is True
    assert state["store_access"] is True
    assert state["marketplace_selling_access"] is True


def test_store_and_selling_can_never_disagree(cur):
    """The two surfaces drifted apart because they were two booleans."""
    for status in ("draft", "submitted", "approved", "rejected", "suspended"):
        cur.execute("DELETE FROM marketplace_sellers")
        cur.execute("DELETE FROM marketplace_merchant_applications")
        _seller(cur, 9, status)
        _application(cur, 9, status)
        state = sas.get_seller_access_state(cur, 9)
        assert state["store_access"] == state["marketplace_selling_access"], status


def test_a_suspended_seller_row_overrides_an_approved_application(cur):
    """Approval is history; suspension is current standing."""
    _application(cur, 4, "approved")
    _seller(cur, 4, "suspended")
    state = sas.get_seller_access_state(cur, 4)
    assert state["seller_application_status"] == sas.SUSPENDED
    assert state["store_access"] is False


def test_a_suspended_seller_keeps_access_to_existing_orders(cur):
    """Fulfilment and refunds are obligations to buyers, not seller privileges."""
    _seller(cur, 5, "suspended")
    state = sas.get_seller_access_state(cur, 5)
    assert state["store_access"] is False
    assert state["can_manage_existing_orders"] is True


def test_a_legacy_pending_review_status_is_not_read_as_unknown(cur):
    """Rows written before the lifecycle module used different words."""
    _application(cur, 6, "pending_review")
    assert sas.get_seller_access_state(cur, 6)["seller_application_status"] == sas.SUBMITTED


def test_the_newest_application_wins_not_the_first(cur):
    """A reapplication must not be masked by the declined row before it."""
    _application(cur, 8, "rejected")
    _application(cur, 8, "submitted")
    assert sas.get_seller_access_state(cur, 8)["seller_application_status"] == sas.SUBMITTED


def test_an_unreadable_database_fails_closed(cur):
    class _Broken:
        def execute(self, *a, **k):
            raise sqlite3.OperationalError("no such table: marketplace_sellers")

        def fetchone(self):
            return None

    state = sas.get_seller_access_state(_Broken(), 1)
    assert state["store_access"] is False
    assert state["seller_approved"] is False
    assert state["degraded"] is True


# --------------------------------------------------------------------------- #
# Card payments — the second, independent axis
# --------------------------------------------------------------------------- #


def test_an_approved_seller_without_stripe_still_gets_the_store(cur):
    """Stripe readiness must never gate Store access."""
    _seller(cur, 10, "approved")
    state = sas.get_seller_access_state(cur, 10)
    assert state["store_access"] is True
    assert state["card_payment_status"] == sas.CARD_SETUP_REQUIRED
    assert state["card_setup_actionable"] is True


def test_no_connected_account_reads_as_setup_required_not_ready(cur):
    """This is production's exact state, and the checkout row that said
    'Temporarily Unavailable' with no way forward."""
    _seller(cur, 11, "approved")
    cur.execute("INSERT INTO seller_payout_accounts (user_id) VALUES (?)", (11,))
    assert sas.get_seller_access_state(cur, 11)["card_payment_status"] == sas.CARD_SETUP_REQUIRED


def test_both_capabilities_granted_reads_ready(cur):
    _seller(cur, 12, "approved")
    cur.execute(
        "INSERT INTO seller_payout_accounts (user_id, connected_account_id, "
        "onboarding_status, charges_enabled, payouts_enabled) VALUES (?,?,?,?,?)",
        (12, "acct_live_1", "completed", 1, 1),
    )
    state = sas.get_seller_access_state(cur, 12)
    assert state["card_payment_status"] == sas.CARD_READY
    assert state["card_setup_actionable"] is False


def test_outstanding_requirements_read_as_action_required(cur):
    _seller(cur, 13, "approved")
    cur.execute(
        "INSERT INTO seller_payout_accounts (user_id, connected_account_id, "
        "onboarding_status, missing_requirements_json) VALUES (?,?,?,?)",
        (13, "acct_1", "completed", '{"currently_due": ["individual.id_number"]}'),
    )
    assert sas.get_seller_access_state(cur, 13)["card_payment_status"] == sas.CARD_ACTION_REQUIRED


def test_a_restricted_account_is_not_presented_as_setup_required(cur):
    """Telling a restricted seller to 'set up payments' loops them through a
    flow they have already finished."""
    _seller(cur, 14, "approved")
    cur.execute(
        "INSERT INTO seller_payout_accounts (user_id, connected_account_id, "
        "onboarding_status) VALUES (?,?,?)",
        (14, "acct_2", "restricted"),
    )
    state = sas.get_seller_access_state(cur, 14)
    assert state["card_payment_status"] == sas.CARD_RESTRICTED
    assert state["card_setup_actionable"] is False


def test_completed_onboarding_with_no_capabilities_reads_under_review(cur):
    _seller(cur, 15, "approved")
    cur.execute(
        "INSERT INTO seller_payout_accounts (user_id, connected_account_id, "
        "onboarding_status, charges_enabled, payouts_enabled) VALUES (?,?,?,?,?)",
        (15, "acct_3", "completed", 0, 0),
    )
    state = sas.get_seller_access_state(cur, 15)
    assert state["card_payment_status"] == sas.CARD_UNDER_REVIEW
    assert state["card_setup_actionable"] is False


def test_half_enabled_reads_under_review_not_ready(cur):
    _seller(cur, 16, "approved")
    cur.execute(
        "INSERT INTO seller_payout_accounts (user_id, connected_account_id, "
        "onboarding_status, charges_enabled, payouts_enabled) VALUES (?,?,?,?,?)",
        (16, "acct_4", "completed", 1, 0),
    )
    assert sas.get_seller_access_state(cur, 16)["card_payment_status"] == sas.CARD_UNDER_REVIEW


def test_the_platform_flag_off_is_reported_as_unavailable_not_as_seller_fault(cur, monkeypatch):
    """There is nothing the seller can do about the kill switch, so they must
    not be shown a button that implies there is."""
    monkeypatch.setenv("MARKETPLACE_CARD_PAYMENTS_ENABLED", "false")
    _seller(cur, 17, "approved")
    cur.execute(
        "INSERT INTO seller_payout_accounts (user_id, connected_account_id, "
        "onboarding_status, charges_enabled, payouts_enabled) VALUES (?,?,?,?,?)",
        (17, "acct_5", "completed", 1, 1),
    )
    state = sas.get_seller_access_state(cur, 17)
    assert state["card_payment_status"] == sas.CARD_UNAVAILABLE
    assert state["card_setup_actionable"] is False
    # and the kill switch must not touch Store access
    assert state["store_access"] is True


def test_an_absent_payout_table_does_not_crash_the_gate(cur):
    """Instances that have never opened payouts have no such table."""
    cur.execute("DROP TABLE seller_payout_accounts")
    _seller(cur, 18, "approved")
    state = sas.get_seller_access_state(cur, 18)
    assert state["store_access"] is True
    assert state["card_payment_status"] == sas.CARD_SETUP_REQUIRED


# --------------------------------------------------------------------------- #
# Route helper
# --------------------------------------------------------------------------- #


def test_require_seller_access_returns_none_for_an_approved_seller(cur):
    _seller(cur, 20, "approved")
    assert sas.require_seller_access(cur, 20) is None


def test_require_seller_access_refuses_a_draft_applicant_with_a_routable_body(cur):
    _application(cur, 21, "draft")
    body = sas.require_seller_access(cur, 21)
    assert body is not None
    # error_code, not code — pulseApi reads error_code and collapses anything
    # else to a generic failure.
    assert body["error_code"] == "seller_not_approved"
    assert body["seller_access"]["seller_application_status"] == sas.DRAFT


def test_every_access_state_is_a_declared_constant(cur):
    """Guards against a typo'd state the client can never match."""
    for status in ("draft", "submitted", "under_review", "information_requested",
                   "resubmitted", "approved", "rejected", "withdrawn", "expired",
                   "suspended"):
        cur.execute("DELETE FROM marketplace_merchant_applications")
        _application(cur, 30, status)
        state = sas.get_seller_access_state(cur, 30)
        assert state["seller_application_status"] in sas.ALL_ACCESS_STATES
        assert state["card_payment_status"] in sas.ALL_CARD_STATES
