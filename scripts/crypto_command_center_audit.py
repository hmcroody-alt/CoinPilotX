#!/usr/bin/env python3
"""Audit PulseSoc Crypto Command Center visibility, routes, and safety."""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

tmp_db = tempfile.NamedTemporaryFile(prefix="pulsesoc-crypto-command-", suffix=".db", delete=False)
tmp_db.close()
os.environ["COINPILOTX_DISABLE_LOCAL_ENV"] = "1"
os.environ["DATABASE_URL"] = f"sqlite:///{tmp_db.name}"
os.environ["SECRET_KEY"] = "crypto-command-audit-secret"
os.environ["FLASK_SECRET_KEY"] = "crypto-command-audit-secret"
os.environ["SESSION_SECRET"] = "crypto-command-audit-secret"
os.environ["FORCE_INIT_DB"] = "1"
os.environ["PULSESOC_CRYPTO_DISABLE_LIVE_MARKETS"] = "1"
os.environ["PULSE_AI_ENABLED"] = "false"
os.environ["PULSE_CRYPTO_AI_ENABLED"] = "false"
os.environ.pop("PULSE_CRYPTO_AI_SAFE_FALLBACK_ENABLED", None)

import bot  # noqa: E402
from services import dashboard_crypto_command_center  # noqa: E402
from services import pulse_dashboard_mission_control  # noqa: E402
from services import pulse_feed_engine  # noqa: E402


EXPECTED_MODULE_ACTIONS = {
    "market_pulse": "View Market Pulse",
    "create_alert": "Create Alert",
    "my_alerts": "Manage Alerts",
    "watchlists": "View Watchlists",
    "ask_ai": "Ask Crypto AI",
    "portfolio": "Review Portfolio",
    "wallet": "Manage Wallet",
    "market_scanner": "Scan Market",
    "whale_alerts": "Track Whales",
    "trending_coins": "View Trending",
    "top_gainers": "View Gainers",
    "top_losers": "View Losers",
    "token_scanner": "Scan Token",
    "crypto_news": "Read Crypto News",
    "economic_calendar": "View Calendar",
    "ai_market_analysis": "Analyze Market",
    "favorite_coins": "View Favorites",
    "recently_viewed": "Continue Watching",
}

EXPECTED_USER_ROUTES = {
    "/dashboard/crypto",
    "/dashboard/crypto/market-pulse",
    "/dashboard/crypto/alerts/create",
    "/dashboard/crypto/alerts",
    "/dashboard/crypto/watchlists",
    "/dashboard/crypto/ask-ai",
    "/dashboard/crypto/portfolio",
    "/dashboard/crypto/wallet",
    "/dashboard/crypto/market-scanner",
    "/dashboard/crypto/whale-alerts",
    "/dashboard/crypto/trending",
    "/dashboard/crypto/gainers",
    "/dashboard/crypto/losers",
    "/dashboard/crypto/token-scanner",
    "/dashboard/crypto/news",
    "/dashboard/crypto/calendar",
    "/dashboard/crypto/ai-analysis",
    "/dashboard/crypto/favorites",
    "/dashboard/crypto/recent",
}

EXPECTED_ADMIN_SECTIONS = {
    "alerts",
    "watchlists",
    "market-data",
    "ai-usage",
    "token-scanner",
    "whale-provider",
    "news-provider",
    "portfolio",
    "wallet-safety",
    "audit",
}

EXPECTED_ROUTE_RULES = {
    "/api/dashboard/crypto/state",
    "/api/crypto/summary",
    "/api/crypto/market-pulse",
    "/api/crypto/alerts",
    "/api/crypto/alerts/<int:alert_id>",
    "/api/crypto/watchlists",
    "/api/crypto/watchlists/<int:watchlist_id>/assets",
    "/api/crypto/watchlists/<int:watchlist_id>/assets/<int:asset_id>",
    "/api/crypto/ask",
    "/api/crypto/ask-ai",
    "/api/crypto/token-scan",
    "/api/crypto/trending",
    "/api/crypto/gainers",
    "/api/crypto/losers",
    "/api/crypto/news",
    "/api/crypto/calendar",
    "/api/crypto/recent",
    "/api/crypto/favorites",
    "/dashboard/crypto",
    "/dashboard/crypto/<path:module_path>",
    "/admin/command-center/crypto",
    "/admin/command-center/crypto/<section_key>",
}

SENSITIVE_TERMS = (
    "private_key",
    "seed_phrase=",
    "mnemonic=",
    "database_url",
    "password_hash",
    "raw_token",
    "raw_push_token",
    "stripe_customer_id",
    "stripe_subscription",
    "command_center_internal_token",
    "filesystem",
    "storage_path",
)


def fail(message: str) -> None:
    print(f"FAIL: {message}")
    raise SystemExit(1)


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        fail(message)


def assert_no_sensitive_leak(text: str, context: str) -> None:
    lowered = text.lower()
    for term in SENSITIVE_TERMS:
        assert_true(term not in lowered, f"{context} does not expose {term}")


def route_rules() -> set[str]:
    return {str(rule.rule) for rule in bot.webhook_app.url_map.iter_rules()}


def ensure_user(cur, user_id: int, email: str, name: str, *, premium: bool = False) -> None:
    now = "2026-06-28T00:00:00"
    cur.execute(
        """
        INSERT OR REPLACE INTO users
        (user_id, username, display_name, email, signup_time, onboarding_complete, alerts_enabled, is_pro, email_verified, profile_visibility)
        VALUES (?, ?, ?, ?, ?, 1, 1, ?, 1, 'public')
        """,
        (user_id, name.lower().replace(" ", "_"), name, email, now, 1 if premium else 0),
    )
    try:
        cur.execute(
            "UPDATE users SET plan=?, subscription_status=?, avatar_url=?, updated_at=? WHERE user_id=?",
            ("premium" if premium else "free", "active" if premium else "inactive", "/static/avatar.png", now, user_id),
        )
    except Exception:
        pass


def setup_data() -> None:
    bot.init_db()
    conn = bot.db()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    ensure_user(cur, 801, "crypto-free@example.test", "Crypto Free")
    ensure_user(cur, 802, "crypto-admin@example.test", "Crypto Admin", premium=True)
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS admin_users (
            id INTEGER PRIMARY KEY,
            user_id INTEGER,
            email TEXT,
            name TEXT,
            role TEXT,
            status TEXT,
            created_at TEXT,
            updated_at TEXT
        )
        """
    )
    cur.execute("PRAGMA table_info(admin_users)")
    admin_columns = {row[1] for row in cur.fetchall()}
    admin_values = {
        "id": 1,
        "user_id": 802,
        "email": "crypto-admin@example.test",
        "name": "Crypto Admin",
        "full_name": "Crypto Admin",
        "display_name": "Crypto Admin",
        "role": "owner",
        "status": "active",
        "password_hash": "audit-password-hash-not-used",
        "must_change_password": 0,
        "created_at": "2026-06-28T00:00:00",
        "updated_at": "2026-06-28T00:00:00",
    }
    insert_columns = [column for column in admin_values if column in admin_columns]
    placeholders = ", ".join("?" for _ in insert_columns)
    cur.execute(
        f"INSERT OR REPLACE INTO admin_users ({', '.join(insert_columns)}) VALUES ({placeholders})",
        tuple(admin_values[column] for column in insert_columns),
    )
    conn.commit()
    conn.close()


def client_for(user_id: int, *, admin_user_id: int | None = None):
    client = bot.webhook_app.test_client()
    with client.session_transaction() as sess:
        sess["account_user_id"] = user_id
        if admin_user_id is not None:
            sess["admin_user_id"] = admin_user_id
    return client


def json_body(response):
    return json.loads(response.get_data(as_text=True))


def run() -> None:
    setup_data()
    rules = route_rules()
    for route in EXPECTED_ROUTE_RULES:
        assert_true(route in rules, f"{route} route is registered")

    assert_true(pulse_feed_engine.normalize_feed("crypto") == "crypto", "crypto feed is registered")
    assert_true(pulse_feed_engine.normalize_feed("crypto-feed") == "crypto", "crypto feed alias works")

    assert_true(len(dashboard_crypto_command_center.MODULES) == 18, "18 crypto modules are registered")
    assert_true(set(EXPECTED_MODULE_ACTIONS) == set(dashboard_crypto_command_center.MODULE_BY_KEY), "all crypto modules are present")
    for key, expected_action in EXPECTED_MODULE_ACTIONS.items():
        module = dashboard_crypto_command_center.MODULE_BY_KEY[key]
        assert_true(module["action"] == expected_action, f"{key} has contextual action label")
        assert_true(module["action"] != "Open", f"{key} does not use generic Open")
        assert_true(module["route"].startswith("/dashboard/crypto"), f"{key} routes into Crypto Command Center")

    admin_sections = {section["key"] for section in dashboard_crypto_command_center.ADMIN_SECTIONS}
    assert_true(admin_sections == EXPECTED_ADMIN_SECTIONS, "all crypto backend sections are registered")

    conn = bot.db()
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM users WHERE user_id=801")
        user = dict(cur.fetchone())
        state = dashboard_crypto_command_center.build_crypto_state(conn, user)
        assert_true("hub" in state and "cards" in state and "modules" in state, "crypto state includes hub, cards, modules")
        assert_true(len(state["cards"]) == 18, "crypto state returns 18 cards")
        serialized_state = json.dumps(state)
        assert_true("LogiNexus" not in serialized_state, "internal design name is invisible in crypto state")
        assert_no_sensitive_leak(serialized_state, "crypto state")
        for card in state["cards"]:
            key = card["key"]
            assert_true(card.get("cta_label") == EXPECTED_MODULE_ACTIONS[key], f"{key} cta is contextual")
            assert_true(card.get("state") in dashboard_crypto_command_center.STRICT_STATES, f"{key} state is strict")
            assert_true(card.get("state") != "ACTIVE", f"{key} does not use fake ACTIVE")
            assert_true(not (card.get("state") == "COMING_SOON" and card.get("cta_label") == "Open"), f"{key} coming-soon action is not fake")

        dashboard = pulse_dashboard_mission_control.build_mission_control_dashboard(conn, user)
        crypto_categories = [category for category in dashboard.get("categories") or [] if category.get("name") == "Crypto Command Center"]
        assert_true(crypto_categories, "Mission Control includes visible Crypto Command Center category")
        assert_true(len(crypto_categories[0].get("widgets") or []) == 18, "Mission Control exposes all crypto widgets")
        for widget in crypto_categories[0].get("widgets") or []:
            assert_true(widget.get("cta_label") != "Open", f"{widget.get('display_name')} avoids generic Open")
            assert_true(widget.get("status_label") != "ACTIVE", f"{widget.get('display_name')} avoids ACTIVE")
            assert_true((widget.get("route") or "").startswith("/dashboard/crypto"), f"{widget.get('display_name')} has real crypto route")
    finally:
        conn.close()

    user_client = client_for(801)
    for route in EXPECTED_USER_ROUTES:
        response = user_client.get(route)
        assert_true(response.status_code == 200, f"{route} loads")
        body = response.get_data(as_text=True)
        assert_true("Crypto Command Center" in body or "CRYPTO COMMAND CENTER" in body, f"{route} renders crypto shell")
        assert_true("LogiNexus" not in body, f"{route} keeps internal name invisible")
        assert_no_sensitive_leak(body, route)

    pulse_response = user_client.get("/pulse?feed=crypto")
    assert_true(pulse_response.status_code == 200, "/pulse?feed=crypto loads")
    pulse_body = pulse_response.get_data(as_text=True)
    assert_true('<button data-feed="crypto">Crypto</button>' in pulse_body, "Pulse feed exposes Crypto tab")
    assert_true('data-feed="crypto"' in pulse_body, "Pulse feed activates crypto state")

    summary = user_client.get("/api/crypto/summary")
    assert_true(summary.status_code == 200, "crypto summary API loads")
    assert_no_sensitive_leak(summary.get_data(as_text=True), "crypto summary API")

    alert_create = user_client.post(
        "/api/crypto/alerts",
        json={"assetSymbol": "BTC", "condition": "above", "targetValue": 150000, "notifyPush": True, "notifyEmail": False, "notifyInApp": True},
    )
    assert_true(alert_create.status_code == 200, "alert create succeeds")
    alert_id = int(json_body(alert_create)["alert_id"])
    other_client = client_for(802)
    forbidden_delete = other_client.delete(f"/api/crypto/alerts/{alert_id}")
    assert_true(forbidden_delete.status_code == 400, "another user cannot delete owner-scoped alert")
    alert_patch = user_client.patch(f"/api/crypto/alerts/{alert_id}", json={"status": "paused"})
    assert_true(alert_patch.status_code == 200, "owner can pause alert")

    watchlist_create = user_client.post("/api/crypto/watchlists", json={"name": "Audit Watchlist"})
    assert_true(watchlist_create.status_code == 200, "watchlist create succeeds")
    watchlist_id = int(json_body(watchlist_create)["watchlist_id"])
    asset_create = user_client.post(f"/api/crypto/watchlists/{watchlist_id}/assets", json={"assetSymbol": "ETH", "notes": "audit"})
    assert_true(asset_create.status_code == 200, "watchlist asset add succeeds")
    asset_id = int(json_body(asset_create)["asset_id"])
    forbidden_asset_delete = other_client.delete(f"/api/crypto/watchlists/{watchlist_id}/assets/{asset_id}")
    assert_true(forbidden_asset_delete.status_code == 400, "another user cannot delete watchlist asset")

    ask_page = user_client.get("/dashboard/crypto/ask-ai")
    assert_true(ask_page.status_code == 200, "Ask Crypto AI page loads")
    ask_body = ask_page.get_data(as_text=True)
    assert_true("Disabled (feature flag)" in ask_body, "Ask Crypto AI page shows disabled feature-flag state")
    assert_true("title='AI not enabled yet'" in ask_body, "disabled AI controls expose required tooltip")
    assert_true('data-crypto-ai-enabled="0"' in ask_body, "Ask Crypto AI form is gated off")
    assert_true("data-disabled-prompt" in ask_body, "quick actions are rendered as disabled prompts")
    assert_true("data-prompt=" not in ask_body, "disabled quick actions have no active prompt handlers")
    assert_true("name=\"question\"" in ask_body and "name=\"asset\"" in ask_body, "question and asset fields are present")
    assert_true("Crypto AI is not enabled yet" not in ask_body, "old placeholder state text is removed")

    ai_response = user_client.post("/api/crypto/ask", json={"question": "Why is Bitcoin moving today?", "asset": "BTC"})
    assert_true(ai_response.status_code == 503, "crypto AI primary endpoint is gated when disabled")
    ai_payload = json_body(ai_response)
    assert_true(ai_payload.get("ok") is False, "disabled crypto AI does not pretend success")
    assert_true(ai_payload.get("state") == "DISABLED", "crypto AI is disabled by feature flag")
    assert_true(not ai_payload.get("analysis"), "disabled crypto AI does not return fake analysis")
    assert_true("not financial advice" in (ai_payload.get("disclaimer") or "").lower(), "crypto AI includes safety disclaimer")

    legacy_ai_response = user_client.post("/api/crypto/ask-ai", json={"prompt": "Compare BTC and ETH.", "assetSymbol": "ETH"})
    assert_true(legacy_ai_response.status_code == 503, "legacy crypto AI endpoint is also gated when disabled")
    legacy_ai_payload = json_body(legacy_ai_response)
    assert_true(legacy_ai_payload.get("state") == "DISABLED", "legacy endpoint uses same disabled state")
    assert_true("not financial advice" in (ai_payload.get("disclaimer") or "").lower(), "crypto AI includes safety disclaimer")

    os.environ["PULSE_CRYPTO_AI_ENABLED"] = "true"
    os.environ.pop("PULSE_CRYPTO_AI_SAFE_FALLBACK_ENABLED", None)
    limited_page = user_client.get("/dashboard/crypto/ask-ai")
    limited_body = limited_page.get_data(as_text=True)
    assert_true(limited_page.status_code == 200, "limited Crypto AI page loads")
    assert_true("Limited (beta)" in limited_body, "approved fallback is the default enabled state")
    assert_true("Crypto AI is flagged on, but no approved AI router or safe fallback is configured." not in limited_body, "limited page omits the obsolete unavailable state")
    assert_true('data-crypto-ai-enabled="1"' in limited_body, "limited form is enabled")
    assert_true(limited_body.count("data-prompt=") == 4, "all four quick actions are active in limited state")

    limited_response = user_client.post("/api/crypto/ask", json={"question": "Compare BTC and ETH", "asset": "BTC"})
    assert_true(limited_response.status_code == 200, "approved safe fallback answers when its flag is absent")
    limited_payload = json_body(limited_response)
    assert_true(limited_payload.get("state") == "LIMITED", "safe fallback reports limited beta state")
    assert_true(limited_payload.get("analysis"), "safe fallback returns computed analysis")
    assert_true(limited_payload.get("source") == "safe_rule_based_fallback", "safe fallback labels its source")
    assert_true("not financial advice" in (limited_payload.get("disclaimer") or "").lower(), "limited response remains educational")

    limited_legacy_response = user_client.post("/api/crypto/ask-ai", json={"prompt": "Why is Ethereum moving?", "assetSymbol": "ETH"})
    assert_true(limited_legacy_response.status_code == 200, "legacy alias uses the same approved fallback")
    assert_true(json_body(limited_legacy_response).get("state") == "LIMITED", "legacy alias reports limited state")
    audit_conn = bot.db()
    try:
        stored_asset = audit_conn.execute(
            "SELECT asset_symbol FROM crypto_ai_queries WHERE user_id=? ORDER BY id DESC LIMIT 1",
            (801,),
        ).fetchone()
    finally:
        audit_conn.close()
    assert_true(stored_asset and stored_asset[0] == "ETH", "asset input reaches the backend through the legacy alias")

    os.environ["PULSE_CRYPTO_AI_SAFE_FALLBACK_ENABLED"] = "false"
    gated_response = user_client.post("/api/crypto/ask", json={"question": "Summarize crypto market", "asset": "BTC"})
    assert_true(gated_response.status_code == 503, "explicit fallback kill switch disables Crypto AI")
    gated_payload = json_body(gated_response)
    assert_true(gated_payload.get("state") == "DISABLED", "explicit fallback kill switch reports disabled")
    assert_true(not gated_payload.get("analysis"), "disabled fallback does not return fake analysis")

    os.environ["PULSE_CRYPTO_AI_SAFE_FALLBACK_ENABLED"] = "true"
    explicit_response = user_client.post("/api/crypto/ask", json={"question": "Summarize crypto market", "asset": "BTC"})
    assert_true(explicit_response.status_code == 200, "explicit fallback enablement returns limited response")
    assert_true(json_body(explicit_response).get("state") == "LIMITED", "explicit fallback enablement reports limited")
    os.environ["PULSE_CRYPTO_AI_ENABLED"] = "false"
    os.environ.pop("PULSE_CRYPTO_AI_SAFE_FALLBACK_ENABLED", None)

    scan_response = user_client.post("/api/crypto/token-scan", json={"symbol": "DOGE"})
    assert_true(scan_response.status_code == 200, "token scanner returns beta response")
    assert_true(json_body(scan_response).get("state") == "BETA", "token scanner is truthful beta")

    for route in ("/api/crypto/trending", "/api/crypto/gainers", "/api/crypto/losers", "/api/crypto/news", "/api/crypto/calendar", "/api/crypto/recent", "/api/crypto/favorites"):
        response = user_client.get(route)
        assert_true(response.status_code == 200, f"{route} loads")
        assert_no_sensitive_leak(response.get_data(as_text=True), route)

    non_admin = user_client.get("/admin/command-center/crypto")
    assert_true(non_admin.status_code in {302, 401, 403}, "non-admin is blocked from backend Crypto Command Center")
    admin_client = client_for(802, admin_user_id=1)
    admin_response = admin_client.get("/admin/command-center/crypto")
    assert_true(admin_response.status_code == 200, "admin Crypto Command Center loads")
    admin_body = admin_response.get_data(as_text=True)
    assert_true("Crypto Command Center" in admin_body, "admin crypto title renders")
    assert_true("LogiNexus" not in admin_body, "admin crypto page keeps internal name invisible")
    assert_no_sensitive_leak(admin_body, "admin crypto command center")
    for section in EXPECTED_ADMIN_SECTIONS:
        response = admin_client.get(f"/admin/command-center/crypto/{section}")
        assert_true(response.status_code == 200, f"/admin/command-center/crypto/{section} loads")
        body = response.get_data(as_text=True)
        assert_true("Operational Console" in body, f"{section} backend surface renders")
        assert_no_sensitive_leak(body, section)

    print("PASS: Crypto Command Center audit passed")


if __name__ == "__main__":
    try:
        run()
    finally:
        try:
            Path(tmp_db.name).unlink(missing_ok=True)
        except Exception:
            pass
