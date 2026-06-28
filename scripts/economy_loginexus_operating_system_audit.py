#!/usr/bin/env python3
"""Audit the PulseSoc Economy operating system wiring.

The internal design philosophy name is allowed in this audit filename and
terminal output, but it must not appear in rendered product UI.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

tmp_db = tempfile.NamedTemporaryFile(prefix="pulsesoc-economy-os-", suffix=".db", delete=False)
tmp_db.close()
os.environ["COINPILOTX_DISABLE_LOCAL_ENV"] = "1"
os.environ["DATABASE_URL"] = f"sqlite:///{tmp_db.name}"
os.environ["SECRET_KEY"] = "economy-os-audit-secret"
os.environ["FLASK_SECRET_KEY"] = "economy-os-audit-secret"
os.environ["SESSION_SECRET"] = "economy-os-audit-secret"
os.environ["FORCE_INIT_DB"] = "1"

import bot  # noqa: E402
from services import backend_management_registry  # noqa: E402
from services import dashboard_economy_command_center  # noqa: E402
from services import pulse_dashboard_mission_control  # noqa: E402


EXPECTED_ACTIONS = {
    "wallet": "Manage Wallet",
    "earnings": "View Earnings",
    "marketplace": "Marketplace Center",
    "seller-tools": "Become a Seller",
    "subscriptions": "Manage Subscription",
    "premium": "Premium Center",
    "creator-revenue": "Revenue Center",
    "payouts": "Payout Center",
    "revenue-analytics": "Revenue Intelligence",
    "ad-revenue": "Advertising Revenue",
    "affiliate-revenue": "Affiliate Center",
    "store-analytics": "Store Intelligence",
    "product-intelligence": "Product Intelligence",
    "revenue-forecast": "Revenue Forecast",
}

EXPECTED_ADMIN_SECTIONS = {
    "wallets",
    "transactions",
    "orders",
    "sellers",
    "products",
    "subscriptions",
    "premium",
    "payouts",
    "revenue",
    "affiliate",
    "marketplace",
    "taxes",
    "fraud",
    "refunds",
    "chargebacks",
    "payment-providers",
    "stripe",
    "apple-iap",
    "google-play-billing",
    "audit",
}

EXPECTED_ROUTES = {
    "/api/dashboard/economy/state",
    "/dashboard/economy",
    "/dashboard/economy/<subsystem_key>",
    "/admin/economy-command-center",
    "/admin/economy-command-center/<section_key>",
}

SENSITIVE_TERMS = (
    "private_key",
    "database_url",
    "password_hash",
    "raw_token",
    "raw_push_token",
    "filesystem",
    "storage_path",
    "command_center_internal_token",
    "stripe_customer_id",
    "stripe_subscription_id",
    "payment_method_id",
    "card_number",
    "bank_account",
    "webhook_secret",
)


def fail(message: str) -> None:
    print(f"FAIL: {message}")
    raise SystemExit(1)


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        fail(message)


def route_rules() -> set[str]:
    return {str(rule.rule) for rule in bot.webhook_app.url_map.iter_rules()}


def route_resolves(path: str) -> bool:
    adapter = bot.webhook_app.url_map.bind("localhost")
    try:
        adapter.match(path, method="GET")
        return True
    except Exception:
        return False


def assert_no_sensitive_leak(text: str, context: str) -> None:
    lowered = text.lower()
    for term in SENSITIVE_TERMS:
        assert_true(term not in lowered, f"{context} does not expose {term}")


def ensure_user(cur: sqlite3.Cursor, user_id: int, email: str, name: str, *, premium: bool = False) -> None:
    now = "2026-06-27T00:00:00"
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
            "UPDATE users SET plan=?, subscription_status=?, seller_status=?, avatar_url=?, updated_at=? WHERE user_id=?",
            ("premium" if premium else "free", "active" if premium else "inactive", "approved", "/static/avatar.png", now, user_id),
        )
    except Exception:
        pass


def setup_data() -> None:
    bot.init_db()
    conn = bot.db()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    ensure_user(cur, 401, "economy-user@example.test", "Economy User", premium=False)
    ensure_user(cur, 402, "economy-admin@example.test", "Economy Admin", premium=True)
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
        "user_id": 402,
        "email": "economy-admin@example.test",
        "name": "Economy Admin",
        "full_name": "Economy Admin",
        "display_name": "Economy Admin",
        "role": "owner",
        "status": "active",
        "password_hash": "audit-password-hash-not-used",
        "must_change_password": 0,
        "password_changed_at": "2026-06-27T00:00:00",
        "created_at": "2026-06-27T00:00:00",
        "updated_at": "2026-06-27T00:00:00",
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


def run() -> None:
    setup_data()
    rules = route_rules()
    for route in EXPECTED_ROUTES:
        assert_true(route in rules, f"{route} route is registered")

    strict_states = dashboard_economy_command_center.STRICT_STATES
    for state in ("READY", "ACTION REQUIRED", "REVIEW", "WARNING", "LOCKED", "PREMIUM", "ADMIN", "PARTIAL", "BETA", "COMING SOON"):
        assert_true(state in strict_states, f"{state} strict economy state is supported")

    subsystem_keys = set(dashboard_economy_command_center.SUBSYSTEMS_BY_KEY)
    assert_true(set(EXPECTED_ACTIONS).issubset(subsystem_keys), "all Economy subsystems are registered")
    for key, action in EXPECTED_ACTIONS.items():
        blueprint = dashboard_economy_command_center.SUBSYSTEMS_BY_KEY[key]
        assert_true(blueprint["action"] == action, f"{key} has contextual action label")
        assert_true(blueprint["action"] != "Open", f"{key} avoids generic Open")
        assert_true(blueprint["route"].startswith("/dashboard/economy/"), f"{key} uses dashboard Economy route")
        assert_true("LogiNexus" not in json.dumps(blueprint), f"{key} public blueprint keeps internal naming invisible")

    admin_keys = {section["key"] for section in dashboard_economy_command_center.ECONOMY_SECTIONS}
    assert_true(EXPECTED_ADMIN_SECTIONS == admin_keys, "all backend Economy sections are represented")

    conn = bot.db()
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM users WHERE user_id=401")
        free_user = dict(cur.fetchone())
        state = dashboard_economy_command_center.build_economy_state(conn, free_user)
        assert_true("hub" in state and "cards" in state and "event_mesh" in state, "Economy state includes hub, cards, and event mesh")
        assert_true(len(state["cards"]) == len(EXPECTED_ACTIONS), "Economy state returns every user card")
        serialized_state = json.dumps(state)
        assert_true("LogiNexus" not in serialized_state, "state payload keeps internal naming invisible")
        assert_no_sensitive_leak(serialized_state, "state payload")

        required_hub_fields = {
            "wallet_balance",
            "pending_earnings",
            "available_earnings",
            "marketplace_revenue",
            "creator_revenue",
            "subscription_revenue",
            "estimated_future_revenue",
            "seller_status",
            "trust_score",
            "payment_health",
            "fraud_risk",
            "payout_readiness",
            "tax_status",
            "active_orders",
            "pending_orders",
            "refund_queue",
            "payment_failures",
            "disputes",
            "revenue_trend",
            "ai_financial_summary",
        }
        assert_true(required_hub_fields.issubset(set(state["hub"])), "Economy Hub exposes required safe finance fields")

        for card in state["cards"]:
            key = card.get("key")
            assert_true(key in EXPECTED_ACTIONS, f"{key} card key is expected")
            assert_true(card.get("cta_label") == EXPECTED_ACTIONS[key], f"{key} card action is contextual")
            assert_true(card.get("state") in strict_states, f"{key} card state is strict")
            assert_true(card.get("state") != "ACTIVE", f"{key} card does not use ACTIVE")
            assert_true((card.get("route") or "").startswith("/dashboard/economy/"), f"{key} has real route")

        dashboard = pulse_dashboard_mission_control.build_mission_control_dashboard(conn, free_user)
        economy_categories = [category for category in dashboard.get("categories") or [] if category.get("name") == "Economy & Earnings"]
        assert_true(economy_categories, "Mission Control includes Economy & Earnings category")
        widgets = economy_categories[0].get("widgets") or []
        assert_true(len(widgets) >= len(EXPECTED_ACTIONS), "Mission Control Economy widgets render")
        for widget in widgets:
            assert_true(widget.get("cta_label") != "Open", f"{widget.get('display_name')} avoids generic Open")
            assert_true(widget.get("status_label") != "ACTIVE", f"{widget.get('display_name')} avoids ACTIVE state")
            assert_true(str(widget.get("route") or "").startswith("/dashboard/economy/"), f"{widget.get('display_name')} routes to Economy subsystem")
        assert_true("economy_command_center" in dashboard, "Mission Control payload includes Economy command state")
    finally:
        conn.close()

    assert_true("economy" in backend_management_registry.REQUIRED_MODULES, "backend registry includes Economy module")
    assert_true("economy" in backend_management_registry.MODULE_OPERATING_BLUEPRINTS, "backend registry includes Economy operating blueprint")
    registry_routes = {
        feature.route
        for feature in backend_management_registry.FEATURES
        if feature.category == "economy"
    }
    for section in dashboard_economy_command_center.ECONOMY_SECTIONS:
        assert_true(section["route"] in registry_routes, f"{section['key']} has backend registry route")

    user_client = client_for(401)
    for route in ("/dashboard/economy", "/api/dashboard/economy/state"):
        response = user_client.get(route)
        assert_true(response.status_code == 200, f"{route} loads for authenticated user")
        body = response.get_data(as_text=True)
        assert_true("LogiNexus" not in body, f"{route} keeps internal naming invisible")
        assert_no_sensitive_leak(body, route)
    for key, action in EXPECTED_ACTIONS.items():
        response = user_client.get(f"/dashboard/economy/{key}")
        assert_true(response.status_code == 200, f"/dashboard/economy/{key} loads")
        body = response.get_data(as_text=True)
        assert_true(action in body, f"{key} renders contextual button")
        assert_true("LogiNexus" not in body, f"{key} keeps internal naming invisible")
        assert_no_sensitive_leak(body, key)

    non_admin = user_client.get("/admin/economy-command-center")
    assert_true(non_admin.status_code in {302, 401, 403}, "non-admin is blocked from Economy admin")

    admin_client = client_for(402, admin_user_id=1)
    admin_page = admin_client.get("/admin/economy-command-center")
    assert_true(admin_page.status_code == 200, "admin Economy command center loads")
    admin_body = admin_page.get_data(as_text=True)
    assert_true("Economy Command Center" in admin_body, "admin page renders title")
    assert_true("LogiNexus" not in admin_body, "admin page keeps internal naming invisible")
    assert_no_sensitive_leak(admin_body, "admin command center")
    for key in EXPECTED_ADMIN_SECTIONS:
        response = admin_client.get(f"/admin/economy-command-center/{key}")
        assert_true(response.status_code == 200, f"/admin/economy-command-center/{key} loads")
        body = response.get_data(as_text=True)
        assert_true("Security Boundary" in body, f"{key} renders security boundary")
        assert_true("LogiNexus" not in body, f"{key} keeps internal naming invisible")
        assert_no_sensitive_leak(body, key)

    for href in (
        "/admin/command-center/economy",
        "/admin/payments-command-center",
        "/admin/financial-audit",
        "/admin/transactions",
        "/admin/monetization",
        "/admin/pulse-moderation",
        "/admin/pulse-analytics",
        "/admin/audit-logs",
        "/admin/security",
        "/admin/system",
        "/admin/launch-readiness",
        "/admin/premium-command",
        "/admin/revenue-analytics",
    ):
        assert_true(route_resolves(href), f"{href} supporting admin route exists")

    print("PASS: Economy operating system audit passed")


if __name__ == "__main__":
    try:
        run()
    finally:
        try:
            Path(tmp_db.name).unlink(missing_ok=True)
        except Exception:
            pass
