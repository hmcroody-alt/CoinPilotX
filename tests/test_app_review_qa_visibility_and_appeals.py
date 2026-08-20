"""App Review fixes — item 4 (QA account hiding) and item 9 (appeals repair).

Function-level tests (no full bot.py boot):
  - discovery_visible_sql predicate excludes hidden_from_discovery=1 and
    'disabled_qa'/'deleted'/'suspended' accounts and keeps normal actives
  - strike appeal service: happy path, duplicate-appeal rejection, non-owner
    PermissionError (mapped to 403 by the route), not-found LookupError
  - admin strike appeal decision writes reviewed fields + syncs account_strikes
  - verification_appeals ledger leaves 'submitted': sync_verification_appeal_decision
    and admin_decide_verification both write reviewed_by/reviewed_at/decision_reason

All tests run against fresh in-memory SQLite; nothing touches coinpilotx.db.
"""

import os
import re
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services import schema_guard  # noqa: E402
from services import dashboard_account_command_center as center  # noqa: E402
from services.discovery_visibility import (  # noqa: E402
    HIDDEN_ACCOUNT_STATUSES,
    discovery_visible_sql,
)

STRIKE_REFERENCE_RE = re.compile(r"^SA-\d{4}-[0-9a-f]{6}$")


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
            full_name TEXT,
            account_status TEXT DEFAULT 'active',
            hidden_from_discovery INTEGER DEFAULT 0,
            verified_badge INTEGER DEFAULT 0,
            updated_at TEXT
        )
        """
    )
    return conn


def _make_verification_appeals(conn):
    # Same DDL as services/pulsesoc_dashboard_centers.ensure_tables.
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS verification_appeals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            request_id INTEGER,
            user_id INTEGER NOT NULL,
            appeal_text TEXT,
            status TEXT DEFAULT 'submitted',
            reviewed_by INTEGER,
            reviewed_at TEXT,
            decision_reason TEXT,
            created_at TEXT
        )
        """
    )


# ---------------------------------------------------------------------------
# Item 4 — discovery visibility predicate
# ---------------------------------------------------------------------------

def test_discovery_predicate_excludes_hidden_and_qa_statuses():
    conn = _fresh_conn()
    rows = [
        (1, "normal_active", "active", 0),          # visible
        (2, "smoke_qa_hidden", "active", 1),         # hidden_from_discovery
        (3, "qa_disabled", "disabled_qa", 0),        # QA status
        (4, "gone", "deleted", 0),                   # deleted
        (5, "susp", "suspended", 0),                 # suspended
        (6, "legacy_nulls", None, None),             # NULL columns -> visible
        (7, "restricted_prelaunch", "restricted", 0),  # restricted stays visible
    ]
    for user_id, username, status, hidden in rows:
        conn.execute(
            "INSERT INTO users (user_id, username, account_status, hidden_from_discovery) VALUES (?, ?, ?, ?)",
            (user_id, username, status, hidden),
        )
    visible = {
        row["user_id"]
        for row in conn.execute(f"SELECT u.user_id FROM users u WHERE {discovery_visible_sql('u')}")
    }
    assert visible == {1, 6, 7}
    conn.close()


def test_discovery_predicate_status_list_and_alias_safety():
    assert "disabled_qa" in HIDDEN_ACCOUNT_STATUSES
    assert "deleted" in HIDDEN_ACCOUNT_STATUSES
    fragment = discovery_visible_sql("u")
    assert "u.hidden_from_discovery" in fragment
    assert "u.account_status" in fragment
    with pytest.raises(ValueError):
        discovery_visible_sql("u; DROP TABLE users --")
    with pytest.raises(ValueError):
        discovery_visible_sql("")


# ---------------------------------------------------------------------------
# Item 9c — strike appeals
# ---------------------------------------------------------------------------

def _insert_strike(conn, user_id=7):
    center.ensure_schema(conn)
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO account_strikes (user_id, policy_category, severity, status, public_summary, appeal_status, created_at, updated_at)
        VALUES (?, 'spam', 'low', 'active', 'Automated spam detection', 'available', '2026-08-19T00:00:00', '2026-08-19T00:00:00')
        """,
        (user_id,),
    )
    conn.commit()
    return cur.lastrowid


def test_strike_appeal_happy_path():
    conn = _fresh_conn()
    strike_id = _insert_strike(conn, user_id=7)
    result = center.submit_strike_appeal(conn, 7, strike_id, "This strike was applied in error, please review.")
    assert result["ok"] is True
    assert result["status"] == "submitted"
    assert STRIKE_REFERENCE_RE.match(result["reference"]), result["reference"]
    row = dict(conn.execute("SELECT * FROM account_strike_appeals WHERE id=?", (result["appeal_id"],)).fetchone())
    assert row["status"] == "submitted"
    assert row["user_id"] == 7
    assert row["strike_id"] == strike_id
    assert row["reference"] == result["reference"]
    strike = dict(conn.execute("SELECT appeal_status FROM account_strikes WHERE id=?", (strike_id,)).fetchone())
    assert strike["appeal_status"] == "submitted"
    conn.close()


def test_strike_appeal_duplicate_rejected():
    conn = _fresh_conn()
    strike_id = _insert_strike(conn, user_id=7)
    center.submit_strike_appeal(conn, 7, strike_id, "First appeal with enough detail.")
    with pytest.raises(ValueError):
        center.submit_strike_appeal(conn, 7, strike_id, "Second appeal should be rejected.")
    count = conn.execute("SELECT COUNT(*) AS n FROM account_strike_appeals WHERE strike_id=?", (strike_id,)).fetchone()["n"]
    assert count == 1
    conn.close()


def test_strike_appeal_non_owner_forbidden():
    # The route maps PermissionError -> HTTP 403.
    conn = _fresh_conn()
    strike_id = _insert_strike(conn, user_id=7)
    with pytest.raises(PermissionError):
        center.submit_strike_appeal(conn, 8, strike_id, "I am not the owner of this strike.")
    conn.close()


def test_strike_appeal_not_found_and_short_reason():
    conn = _fresh_conn()
    center.ensure_schema(conn)
    with pytest.raises(LookupError):
        center.submit_strike_appeal(conn, 7, 999999, "Appeal for a missing strike row.")
    strike_id = _insert_strike(conn, user_id=7)
    with pytest.raises(ValueError):
        center.submit_strike_appeal(conn, 7, strike_id, "short")
    conn.close()


def test_strike_appeal_admin_decision_updates_ledger_and_strike():
    conn = _fresh_conn()
    strike_id = _insert_strike(conn, user_id=7)
    submitted = center.submit_strike_appeal(conn, 7, strike_id, "Please take another look at this.")
    decided = center.admin_decide_strike_appeal(conn, submitted["appeal_id"], 42, "approved", "Strike was a false positive.")
    assert decided["ok"] is True
    assert decided["status"] == "approved"
    assert decided["user_id"] == 7
    row = dict(conn.execute("SELECT * FROM account_strike_appeals WHERE id=?", (submitted["appeal_id"],)).fetchone())
    assert row["status"] == "approved"
    assert row["reviewed_by"] == 42
    assert row["reviewed_at"]
    assert row["decision_reason"] == "Strike was a false positive."
    strike = dict(conn.execute("SELECT appeal_status, status FROM account_strikes WHERE id=?", (strike_id,)).fetchone())
    assert strike["appeal_status"] == "approved"
    assert strike["status"] == "revoked"
    with pytest.raises(ValueError):
        center.admin_decide_strike_appeal(conn, submitted["appeal_id"], 42, "rejected", "Already decided.")
    conn.close()


# ---------------------------------------------------------------------------
# Item 9b — verification appeals ledger leaves 'submitted'
# ---------------------------------------------------------------------------

def test_sync_verification_appeal_decision_writes_reviewed_fields():
    conn = _fresh_conn()
    center.ensure_schema(conn)
    _make_verification_appeals(conn)
    conn.execute(
        "INSERT INTO verification_appeals (request_id, user_id, appeal_text, status, created_at) VALUES (11, 7, 'please reconsider', 'submitted', '2026-08-19T00:00:00')"
    )
    conn.commit()
    updated = center.sync_verification_appeal_decision(conn, request_id=11, reviewer_id=42, decision="rejected", reason="Insufficient evidence.")
    assert updated == 1
    row = dict(conn.execute("SELECT * FROM verification_appeals WHERE request_id=11").fetchone())
    assert row["status"] == "rejected"
    assert row["reviewed_by"] == 42
    assert row["reviewed_at"]
    assert row["decision_reason"] == "Insufficient evidence."


def test_admin_decide_verification_syncs_appeal_ledger():
    conn = _fresh_conn()
    center.ensure_schema(conn)
    _make_verification_appeals(conn)
    conn.execute("INSERT INTO users (user_id, username) VALUES (7, 'appealer')")
    request = center.submit_verification_request(conn, 7, "identity", {})
    request_id = request["request_id"]
    conn.execute(
        "INSERT INTO verification_appeals (request_id, user_id, appeal_text, status, created_at) VALUES (?, 7, 'appeal text here', 'submitted', '2026-08-19T00:00:00')",
        (request_id,),
    )
    conn.commit()
    result = center.admin_decide_verification(conn, request_id, 42, "approved", "Verified against documents.")
    assert result["status"] == "approved"
    row = dict(conn.execute("SELECT * FROM verification_appeals WHERE request_id=?", (request_id,)).fetchone())
    assert row["status"] == "approved"
    assert row["reviewed_by"] == 42
    assert row["reviewed_at"]
    assert row["decision_reason"] == "Verified against documents."
    conn.close()
