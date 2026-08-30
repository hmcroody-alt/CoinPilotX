"""Premium usage summary invariants (PREM-USAGE-001..).

Defends the mission's golden rule for the Command Center "usage" and
"recommended" modules: every number is a live count from the domain table the
feature itself reads, an unmeasurable source is OMITTED (never zero-filled),
and recommendations exist only for members and only for sellable capabilities.
"""

import os
import sys
import tempfile

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="busos_usage_"), "test.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from services import db  # noqa: E402
from services.business_os.entitlements import schema as sch  # noqa: E402
from services.business_os.entitlements import usage_summary as us  # noqa: E402

UID = 7001


def setup_module():
    conn = db.connect()
    conn.execute(
        "CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, "
        "account_status TEXT DEFAULT 'active', access_enabled INTEGER DEFAULT 1)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS alert_rules (id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "user_id INTEGER, active INTEGER DEFAULT 1, deleted_at TEXT, "
        "advanced_conditions TEXT)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS alert_events (id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "user_id INTEGER, created_at TEXT)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS portfolio_items (id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "user_id INTEGER)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS pulse_profile_themes (id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "user_id INTEGER, theme_key TEXT, active INTEGER DEFAULT 1, updated_at TEXT)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS pulse_premium_profiles (user_id INTEGER PRIMARY KEY, "
        "aura_style TEXT, status TEXT)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS verification_requests (id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "user_id INTEGER, verification_type TEXT, status TEXT)"
    )
    conn.commit()
    conn.close()
    sch.ensure_ready()


def _signals_by_key(summary):
    return {s["key"]: s for s in summary["signals"]}


# PREM-USAGE-001 --------------------------------------------------------------
def test_counts_are_live_queries_of_domain_tables():
    conn = db.connect()
    conn.execute("INSERT INTO alert_rules (user_id, advanced_conditions) VALUES (?, ?)",
                 (UID, '{"operator":"AND","conditions":[{"type":"price_above","threshold":1}]}'))
    conn.execute("INSERT INTO alert_rules (user_id, advanced_conditions) VALUES (?, NULL)", (UID,))
    conn.execute("INSERT INTO alert_rules (user_id, advanced_conditions, deleted_at) VALUES (?, ?, '2026-01-01')",
                 (UID, '{"operator":"OR","conditions":[]}'))
    for _ in range(4):
        conn.execute("INSERT INTO portfolio_items (user_id) VALUES (?)", (UID,))
    conn.commit()
    conn.close()

    summary = us.summarize(UID, is_member=True)
    sig = _signals_by_key(summary)
    assert sig["advanced_alert_rules"]["value"] == 1  # deleted + NULL excluded
    assert sig["portfolio_holdings"]["value"] == 4
    assert sig["portfolio_holdings"]["beyond_free_limit"] is True
    assert summary["provenance"] == "live_counts"


# PREM-USAGE-002 --------------------------------------------------------------
def test_unmeasurable_source_is_omitted_not_zero_filled(monkeypatch):
    def _boom(uid):
        raise RuntimeError("no such table")
    monkeypatch.setattr(us, "_sig_profile_theme", _boom)
    monkeypatch.setattr(
        us, "_SIGNAL_SOURCES",
        tuple((n, _boom if n == "profile_theme" else f) for n, f in us._SIGNAL_SOURCES),
    )
    summary = us.summarize(UID, is_member=True)
    keys = set(_signals_by_key(summary))
    assert "profile_theme" not in keys
    assert "profile_theme" in summary["omitted"]


# PREM-USAGE-003 --------------------------------------------------------------
def test_recommendations_only_for_members():
    assert us.summarize(UID, is_member=False)["recommendations"] == []


# PREM-USAGE-004 --------------------------------------------------------------
def test_recommendation_derives_from_real_unused_signal():
    summary = us.summarize(UID, is_member=True)
    reasons = {r["reason"] for r in summary["recommendations"]}
    # UID has an advanced rule and >3 holdings, so neither is recommended...
    assert "no_advanced_rules" not in reasons
    assert "within_free_ceiling" not in reasons
    # ...but has no theme, no aura, no blue-check application.
    assert "no_theme_set" in reasons
    assert "no_effect_set" in reasons
    assert "not_applied" in reasons
    # Setting a theme removes exactly that recommendation.
    conn = db.connect()
    conn.execute(
        "INSERT INTO pulse_profile_themes (user_id, theme_key, active, updated_at) "
        "VALUES (?, 'deep_space', 1, '2026-08-01')", (UID,))
    conn.commit(); conn.close()
    reasons2 = {r["reason"] for r in us.summarize(UID, is_member=True)["recommendations"]}
    assert "no_theme_set" not in reasons2


# PREM-USAGE-005 --------------------------------------------------------------
def test_usage_center_payload_is_honest():
    from services.business_os.entitlements import premium_api as papi
    conn = db.connect()
    conn.execute("INSERT OR REPLACE INTO users (user_id) VALUES (?)", (UID,))
    conn.commit(); conn.close()
    status, body = papi.usage_center(UID)
    assert status == 200
    assert body["ok"] is True
    assert body["usage"]["provenance"] == "live_counts"
    assert "not_verification" in body
    # A free account gets signals but no recommendations.
    assert body["membership"]["is_premium"] is False
    assert body["usage"]["recommendations"] == []


# PREM-USAGE-006 --------------------------------------------------------------
def test_unauthenticated_is_rejected():
    from services.business_os.entitlements import premium_api as papi
    status, body = papi.usage_center(None)
    assert status == 401 and body["ok"] is False
