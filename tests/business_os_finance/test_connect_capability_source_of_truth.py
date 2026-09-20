"""One source of truth for "is this seller's Connect account usable?".

Two tables answered that question and only one of them was kept current.
``connect_account_state`` is the canonical projection of Stripe's own words;
``seller_payout_accounts`` is the legacy table that the checkout capability gate
and the seller payouts screen actually read. The ``account.updated`` webhook
refreshed both, but an explicit server-side refresh
(``connect_accounts.record_account_snapshot``) refreshed only the projection —
so a seller Stripe had just switched off kept a ``charges_enabled = 1`` row in
the table the gate consults.

These tests hold two properties:

1. **One sync path.** Every route by which Stripe truth enters the system lands
   in both tables, from the same normalized values, in the same transaction.
2. **The gate cannot be unlocked by a stale column.** Even with the sync path
   bypassed entirely, the capability gate consults the canonical projection and
   takes the more restrictive answer.

Property 2 is what makes property 1 safe to rely on: the tests below deliberately
write a disagreement the sync path would never produce, and require a refusal.

    python3 -m pytest tests/business_os_finance/test_connect_capability_source_of_truth.py
"""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile

# --- point services.db at a throwaway SQLite file BEFORE importing it ---
_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="fin_sot_"), "test.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB

import pytest  # noqa: E402

from services import db  # noqa: E402
from services import marketplace_card_capability as capability  # noqa: E402
from services import marketplace_payment_pause  # noqa: E402
from services.business_os.payments import connect_accounts, incidents  # noqa: E402


SELLER = 4242
ACCOUNT = "acct_sot_1"


# --------------------------------------------------------------------------- #
# Half one: the sync path, against the real services.db
# --------------------------------------------------------------------------- #

#: The legacy table as `bot.init_db` declares it (bot.py:116022), trimmed to the
#: columns this projection touches plus the unique key. Written out rather than
#: imported because importing bot.py to create one table would drag in the whole
#: 111k-line monolith.
_LEGACY_DDL = """
CREATE TABLE IF NOT EXISTS seller_payout_accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    seller_type TEXT,
    provider TEXT DEFAULT 'stripe',
    connected_account_id TEXT,
    provider_account_id TEXT,
    onboarding_status TEXT DEFAULT 'not_started',
    payouts_enabled INTEGER DEFAULT 0,
    charges_enabled INTEGER DEFAULT 0,
    missing_requirements_json TEXT,
    requirements_json TEXT,
    last_checked_at TEXT,
    last_synced_at TEXT,
    created_at TEXT,
    updated_at TEXT,
    UNIQUE(user_id, seller_type)
)
"""


@pytest.fixture
def store():
    """A clean canonical projection + legacy table on the real services.db."""
    os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB
    connect_accounts.ensure_schema()
    incidents.ensure_schema()
    conn = db.connect()
    conn.execute(_LEGACY_DDL)
    conn.execute("DELETE FROM connect_account_state")
    conn.execute("DELETE FROM seller_payout_accounts")
    conn.execute("DELETE FROM financial_incidents")
    conn.commit()
    conn.close()
    yield


def _seed_legacy(*, account=ACCOUNT, charges=1, payouts=1,
                 onboarding="complete", seller_type="merchant"):
    """A legacy row in the state a finished onboarding leaves behind."""
    conn = db.connect()
    conn.execute(
        "INSERT INTO seller_payout_accounts (user_id, seller_type, provider,"
        " connected_account_id, onboarding_status, payouts_enabled,"
        " charges_enabled, updated_at) VALUES (?,?,?,?,?,?,?,?)",
        (SELLER, seller_type, "stripe", account, onboarding, payouts, charges,
         "2026-01-01T00:00:00Z"),
    )
    conn.commit()
    conn.close()


def _legacy_row(account=ACCOUNT) -> dict:
    conn = db.connect()
    row = conn.execute(
        "SELECT * FROM seller_payout_accounts WHERE connected_account_id=?",
        (account,),
    ).fetchone()
    conn.close()
    return dict(row) if row is not None else {}


def _snapshot(*, charges=True, payouts=True, requirements=None,
              account=ACCOUNT):
    """The dict shape `payment_provider.get_account_status` returns."""
    return {
        "ok": True,
        "provider_account_id": account,
        "charges_enabled": charges,
        "payouts_enabled": payouts,
        "requirements": requirements or {},
        "account": {"details_submitted": True},
    }


def _event(*, charges=True, payouts=True, requirements=None, account=ACCOUNT,
           user_id=str(SELLER)):
    return {
        "id": "evt_sot_1",
        "type": "account.updated",
        "data": {"object": {
            "id": account,
            "object": "account",
            "charges_enabled": charges,
            "payouts_enabled": payouts,
            "details_submitted": True,
            "requirements": requirements or {},
            "metadata": {"user_id": user_id},
        }},
    }


def test_an_explicit_refresh_that_learns_stripe_switched_charges_off_updates_the_gate_row(store):
    """The original bug: the refresh path wrote only the projection.

    A seller finishes onboarding (legacy row: charges on). Stripe later turns
    charges off, and PulseSoc learns it from a server-side pull rather than a
    webhook. Before the fix the projection recorded it and the legacy row — the
    one the checkout gate reads — still said `charges_enabled = 1`.
    """
    _seed_legacy(charges=1, payouts=1)
    connect_accounts.record_account_snapshot(SELLER, _snapshot(charges=False))

    assert _legacy_row()["charges_enabled"] == 0
    assert connect_accounts.get_state(SELLER)["charges_enabled"] is False


def test_an_explicit_refresh_that_learns_payouts_are_off_updates_the_gate_row(store):
    _seed_legacy(charges=1, payouts=1)
    connect_accounts.record_account_snapshot(SELLER, _snapshot(payouts=False))

    assert _legacy_row()["payouts_enabled"] == 0


def test_the_webhook_path_projects_onto_the_legacy_row_too(store):
    """Both entry points share `_upsert`, so both must mirror."""
    _seed_legacy(charges=1, payouts=1)
    connect_accounts.apply_account_updated_event(_event(charges=False))

    assert _legacy_row()["charges_enabled"] == 0


def test_outstanding_requirements_reach_the_legacy_row_as_json(store):
    _seed_legacy()
    connect_accounts.record_account_snapshot(
        SELLER, _snapshot(requirements={"currently_due": ["external_account"]}))

    stored = json.loads(_legacy_row()["requirements_json"])
    assert stored["currently_due"] == ["external_account"]


def test_a_disabled_reason_marks_the_legacy_row_restricted(store):
    """`disabled_reason` is Stripe saying the account is restricted.

    The gate treats that `onboarding_status` value as a refusal, so it is the
    one string in that column this projection is entitled to write.
    """
    _seed_legacy(onboarding="complete")
    connect_accounts.record_account_snapshot(SELLER, _snapshot(
        requirements={"disabled_reason": "requirements.past_due"}))

    assert _legacy_row()["onboarding_status"] == "restricted"


def test_a_clean_account_does_not_overwrite_the_onboarding_status(store):
    """Everything else in that column belongs to the onboarding path."""
    _seed_legacy(onboarding="complete")
    connect_accounts.record_account_snapshot(SELLER, _snapshot())

    assert _legacy_row()["onboarding_status"] == "complete"


def test_the_projection_never_creates_a_legacy_row(store):
    """Creating it belongs to onboarding, which owns `seller_type`.

    A missing row already reads as STRIPE_NOT_CONNECTED — a refusal — so
    update-only is the fail-closed choice, not a gap.
    """
    connect_accounts.record_account_snapshot(SELLER, _snapshot())

    conn = db.connect()
    count = conn.execute(
        "SELECT COUNT(*) FROM seller_payout_accounts").fetchone()[0]
    conn.close()
    assert count == 0
    # ...while the canonical projection did record it.
    assert connect_accounts.get_state(SELLER) is not None


def test_a_row_carrying_the_account_in_provider_account_id_is_still_matched(store):
    """Older rows stored the account id in the other column."""
    conn = db.connect()
    conn.execute(
        "INSERT INTO seller_payout_accounts (user_id, seller_type,"
        " provider_account_id, charges_enabled, payouts_enabled) VALUES (?,?,?,?,?)",
        (SELLER, "merchant", ACCOUNT, 1, 1),
    )
    conn.commit()
    conn.close()

    connect_accounts.record_account_snapshot(SELLER, _snapshot(charges=False))

    conn = db.connect()
    row = dict(conn.execute(
        "SELECT charges_enabled FROM seller_payout_accounts"
        " WHERE provider_account_id=?", (ACCOUNT,)).fetchone())
    conn.close()
    assert row["charges_enabled"] == 0


def test_an_unattributable_account_touches_neither_table(store):
    """An orphan event must not write capability state onto someone."""
    _seed_legacy(charges=1)
    result = connect_accounts.apply_account_updated_event(
        _event(charges=False, user_id=None, account="acct_stranger"))

    assert result.get("orphan") is True
    assert _legacy_row()["charges_enabled"] == 1


# --------------------------------------------------------------------------- #
# Half two: the gate, with the sync path deliberately bypassed
# --------------------------------------------------------------------------- #
#
# These use a bare in-memory cursor, matching tests/marketplace/
# test_card_capability.py. They write disagreements the sync path above would
# never produce, because the point is what happens if it is ever bypassed.

@pytest.fixture
def cur():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("CREATE TABLE marketplace_sellers (user_id INTEGER, status TEXT)")
    cursor.execute(
        "CREATE TABLE seller_payout_accounts (user_id INTEGER, connected_account_id TEXT,"
        " provider_account_id TEXT, charges_enabled INTEGER, payouts_enabled INTEGER,"
        " onboarding_status TEXT, requirements_json TEXT, missing_requirements_json TEXT,"
        " updated_at TEXT, id INTEGER PRIMARY KEY AUTOINCREMENT)")
    cursor.execute(
        "CREATE TABLE connect_account_state (user_id TEXT, connected_account_id TEXT,"
        " charges_enabled INTEGER, payouts_enabled INTEGER, details_submitted INTEGER,"
        " requirements_json TEXT, disabled_reason TEXT)")
    try:
        yield cursor
    finally:
        conn.close()


@pytest.fixture(autouse=True)
def _rail_on(monkeypatch):
    """Rail on, Stripe configured: every refusal below has to be *caused*."""
    monkeypatch.setenv(marketplace_payment_pause.CARD_PAYMENTS_ENABLED_ENV_VAR, "true")
    monkeypatch.setattr(capability, "_stripe_configured", lambda: True)


def _legacy_says_yes(cur, account=ACCOUNT):
    cur.execute("DELETE FROM marketplace_sellers")
    cur.execute("DELETE FROM seller_payout_accounts")
    cur.execute("INSERT INTO marketplace_sellers (user_id,status) VALUES (?,?)",
                (SELLER, "approved"))
    cur.execute(
        "INSERT INTO seller_payout_accounts (user_id,connected_account_id,charges_enabled,"
        "payouts_enabled,onboarding_status,updated_at) VALUES (?,?,?,?,?,?)",
        (SELLER, account, 1, 1, "complete", "2026-09-19"))


def _canonical(cur, *, charges=1, payouts=1, requirements=None,
               disabled_reason="", account=ACCOUNT):
    cur.execute("DELETE FROM connect_account_state")
    cur.execute(
        "INSERT INTO connect_account_state (user_id,connected_account_id,charges_enabled,"
        "payouts_enabled,details_submitted,requirements_json,disabled_reason)"
        " VALUES (?,?,?,?,?,?,?)",
        (str(SELLER), account, charges, payouts, 1,
         json.dumps(requirements or {}), disabled_reason))


def _verdict(cur):
    return capability.evaluate(cur, seller_user_id=SELLER)


def test_both_tables_agreeing_still_takes_a_card(cur):
    """The positive control. Without it every refusal below is meaningless."""
    _legacy_says_yes(cur)
    _canonical(cur)
    assert _verdict(cur)["reason_code"] == capability.AVAILABLE


def test_a_stale_charges_enabled_column_cannot_unlock_card_checkout(cur):
    """The property the mission names, stated directly."""
    _legacy_says_yes(cur)
    _canonical(cur, charges=0)
    decision = _verdict(cur)
    assert decision["card_payments_available"] is False
    assert decision["reason_code"] == capability.CARD_CAPABILITY_DISABLED


def test_a_stale_payouts_enabled_column_cannot_unlock_card_checkout(cur):
    _legacy_says_yes(cur)
    _canonical(cur, payouts=0)
    assert _verdict(cur)["reason_code"] == capability.PAYOUTS_DISABLED


def test_a_canonical_disabled_reason_refuses_however_clean_the_legacy_row_is(cur):
    _legacy_says_yes(cur)
    _canonical(cur, disabled_reason="requirements.past_due")
    assert _verdict(cur)["reason_code"] == capability.STRIPE_REQUIREMENTS_DUE


def test_canonical_outstanding_requirements_refuse(cur):
    _legacy_says_yes(cur)
    _canonical(cur, requirements={"currently_due": ["external_account"]})
    assert _verdict(cur)["reason_code"] == capability.STRIPE_REQUIREMENTS_DUE


def test_the_canonical_row_can_refuse_but_never_promote(cur):
    """Both must pass. A clean projection cannot rescue a legacy refusal.

    Otherwise this second read would be a way *around* the gate rather than a
    second lock on it.
    """
    _legacy_says_yes(cur)
    cur.execute("UPDATE seller_payout_accounts SET charges_enabled=0")
    _canonical(cur, charges=1, payouts=1)
    assert _verdict(cur)["reason_code"] == capability.CARD_CAPABILITY_DISABLED


def test_a_projection_for_a_different_account_is_not_consulted(cur):
    """Matched on the account id, so another seller's state cannot bleed in."""
    _legacy_says_yes(cur)
    _canonical(cur, charges=0, account="acct_somebody_else")
    assert _verdict(cur)["reason_code"] == capability.AVAILABLE


def test_no_canonical_row_leaves_the_legacy_verdict_standing(cur):
    """Silence is not a verdict — the legacy checks already ran."""
    _legacy_says_yes(cur)
    cur.execute("DELETE FROM connect_account_state")
    assert _verdict(cur)["reason_code"] == capability.AVAILABLE


def test_an_absent_projection_table_does_not_break_checkout(cur):
    """A read failure must not 500, and must not change today's answer."""
    cur.execute("DROP TABLE connect_account_state")
    _legacy_says_yes(cur)
    assert _verdict(cur)["reason_code"] == capability.AVAILABLE


def test_the_buyer_is_still_not_told_the_seller_s_business(cur):
    """A canonical refusal must collapse for buyers like every other one."""
    _legacy_says_yes(cur)
    _canonical(cur, charges=0)
    buyer = capability.buyer_view(_verdict(cur))
    assert buyer["card_payments_available"] is False
    assert buyer["reason_code"] != capability.CARD_CAPABILITY_DISABLED


def test_the_canonical_check_is_reached_at_all(cur, monkeypatch):
    """Non-vacuity: prove the gate really calls the canonical read.

    Every refusal above would also be produced by a gate that ignored the
    projection and happened to refuse for another reason, so pin the call.
    """
    seen = []
    real = capability._canonical_row
    monkeypatch.setattr(
        capability, "_canonical_row",
        lambda c, a: (seen.append(a), real(c, a))[1])
    _legacy_says_yes(cur)
    _canonical(cur)
    assert _verdict(cur)["reason_code"] == capability.AVAILABLE
    assert seen == [ACCOUNT]
