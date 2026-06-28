#!/usr/bin/env python3
"""Audit subscription billing routes and backend-backed dashboard actions."""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

tmp_db = tempfile.NamedTemporaryFile(prefix="pulsesoc-subscription-wiring-", suffix=".db", delete=False)
tmp_db.close()
os.environ["COINPILOTX_DISABLE_LOCAL_ENV"] = "1"
os.environ["DATABASE_URL"] = f"sqlite:///{tmp_db.name}"
os.environ["SECRET_KEY"] = "subscription-wiring-audit-secret"
os.environ["FLASK_SECRET_KEY"] = "subscription-wiring-audit-secret"
os.environ["SESSION_SECRET"] = "subscription-wiring-audit-secret"
os.environ["FORCE_INIT_DB"] = "1"
os.environ.pop("STRIPE_SECRET_KEY", None)
os.environ.pop("STRIPE_PRICE_ID", None)

import bot  # noqa: E402


REQUIRED_ROUTES = {
    "/billing/portal",
    "/billing/portal-session",
    "/api/subscriptions/status",
    "/api/subscriptions/checklist",
    "/api/subscriptions/upgrade",
    "/api/subscriptions/downgrade",
    "/api/subscriptions/cancel",
    "/api/subscriptions/resume",
    "/dashboard/economy/<subsystem_key>",
}

ACTION_MARKERS = {
    'data-subscription-action="upgrade"',
    'data-subscription-action="downgrade"',
    'data-subscription-action="portal"',
    'data-subscription-action="cancel"',
    'data-subscription-action="resume"',
    'data-subscription-checklist="subscription_benefits_reviewed"',
    'data-subscription-checklist="billing_history_reviewed"',
    'data-subscription-checklist="cancellation_policy_reviewed"',
    "/api/subscriptions/status",
    "/billing/portal-session",
    "/api/subscriptions/checklist",
}

FORBIDDEN_SUBSCRIPTION_HTML = {
    'data-save-pref="benefits_reviewed"',
    'data-save-pref="billing_history_reviewed"',
    "Open Billing Portal",
    ">Take Action<",
    ">Save For Later<",
}

SENSITIVE_TERMS = {
    "sk_live",
    "sk_test",
    "stripe_customer_id\": \"cus_",
    "stripe_subscription_id\": \"sub_",
    "private_key",
    "database_url",
    "command_center_internal_token",
}


def fail(message: str) -> None:
    print(f"FAIL: {message}")
    raise SystemExit(1)


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        fail(message)


def route_rules() -> set[str]:
    return {str(rule.rule) for rule in bot.webhook_app.url_map.iter_rules()}


def seed_user() -> None:
    bot.init_db()
    conn = bot.db()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute(
        """
        INSERT OR REPLACE INTO users
        (user_id, username, display_name, email, signup_time, onboarding_complete, alerts_enabled, is_pro, email_verified, profile_visibility)
        VALUES (?, ?, ?, ?, ?, 1, 1, 0, 1, 'public')
        """,
        (9101, "subscription_audit", "Subscription Audit", "subscription-audit@example.test", "2026-06-28T00:00:00"),
    )
    try:
        cur.execute(
            "UPDATE users SET plan='free', subscription_status='inactive', stripe_customer_id='cus_should_not_leak', stripe_subscription_id='sub_should_not_leak' WHERE user_id=?",
            (9101,),
        )
    except Exception:
        pass
    conn.commit()
    conn.close()


def client_for(user_id: int, csrf: str = "subscription-audit-csrf"):
    client = bot.webhook_app.test_client()
    with client.session_transaction() as sess:
        sess["account_user_id"] = user_id
        sess["csrf_token"] = csrf
    return client


def assert_no_sensitive_leak(payload: str, context: str) -> None:
    lowered = payload.lower()
    for term in SENSITIVE_TERMS:
        assert_true(term not in lowered, f"{context} must not expose {term}")


def run() -> None:
    seed_user()
    rules = route_rules()
    for route in REQUIRED_ROUTES:
        assert_true(route in rules, f"{route} is registered")

    client = client_for(9101)
    page = client.get("/dashboard/economy/subscriptions")
    assert_true(page.status_code == 200, "subscriptions dashboard route loads")
    html = page.get_data(as_text=True)
    for marker in ACTION_MARKERS:
        assert_true(marker in html, f"subscriptions page includes {marker}")
    for forbidden in FORBIDDEN_SUBSCRIPTION_HTML:
        assert_true(forbidden not in html, f"subscriptions page removed fake behavior: {forbidden}")
    assert_true("Billing portal is unavailable" in html or "Stripe billing portal is not configured" in html, "unconfigured billing portal renders a disabled reason")

    status = client.get("/api/subscriptions/status")
    assert_true(status.status_code == 200, "status endpoint loads")
    status_json = status.get_json() or {}
    assert_true(status_json.get("ok") is True, "status endpoint returns ok")
    assert_true(status_json.get("stripe_customer_id") == "", "status redacts Stripe customer id")
    assert_true(status_json.get("stripe_subscription_id") == "", "status redacts Stripe subscription id")
    assert_no_sensitive_leak(json.dumps(status_json), "subscription status")

    missing_csrf = client.post("/api/subscriptions/checklist", json={"key": "subscription_benefits_reviewed", "value": "true"})
    assert_true(missing_csrf.status_code == 403, "checklist write requires CSRF")
    checked = client.post(
        "/api/subscriptions/checklist",
        json={"key": "subscription_benefits_reviewed", "value": "true"},
        headers={"X-CSRF-Token": "subscription-audit-csrf"},
    )
    assert_true(checked.status_code == 200, "checklist write succeeds with CSRF")
    refreshed = client.get("/api/subscriptions/status")
    refreshed_json = refreshed.get_json() or {}
    assert_true((refreshed_json.get("checklist") or {}).get("subscription_benefits_reviewed") is True, "checklist state persists through backend")

    portal = client.get("/billing/portal")
    assert_true(portal.status_code == 503, "GET billing portal returns explicit unavailable response when Stripe is not configured")
    assert_true("Billing portal not configured" in portal.get_data(as_text=True), "billing portal unavailable response is explicit")

    portal_session = client.post("/billing/portal-session", json={}, headers={"X-CSRF-Token": "subscription-audit-csrf"})
    assert_true(portal_session.status_code == 503, "billing portal session returns explicit unavailable response when Stripe is not configured")
    assert_true((portal_session.get_json() or {}).get("ok") is False, "billing portal session reports failure truthfully")

    for endpoint in (
        "/api/subscriptions/upgrade",
        "/api/subscriptions/downgrade",
        "/api/subscriptions/cancel",
        "/api/subscriptions/resume",
    ):
        response = client.post(endpoint, json={}, headers={"X-CSRF-Token": "subscription-audit-csrf"})
        assert_true(response.status_code in {409, 503}, f"{endpoint} returns explicit backend state, not silent success")
        data = response.get_json() or {}
        assert_true(data.get("ok") is False, f"{endpoint} does not fake success")
        assert_true(data.get("message") or data.get("error"), f"{endpoint} explains failure")

    ios_client = client_for(9101, csrf="subscription-ios-csrf")
    ios_status = ios_client.get("/api/subscriptions/status", headers={"User-Agent": "PulseSocNativeApp/1.0 (ios; iPhone)"})
    assert_true(ios_status.status_code == 200, "iOS status endpoint loads")
    ios_json = ios_status.get_json() or {}
    assert_true(ios_json.get("subscription_status") == "ios_core_only", "iOS status is core-only")
    assert_true(ios_json.get("billing_portal_available") is False, "iOS status disables billing portal")
    assert_true(ios_json.get("has_premium_access") is False, "iOS status does not unlock paid premium")
    assert_no_sensitive_leak(json.dumps(ios_json), "iOS subscription status")
    ios_upgrade = ios_client.post(
        "/api/subscriptions/upgrade",
        json={},
        headers={"X-CSRF-Token": "subscription-ios-csrf", "User-Agent": "PulseSocNativeApp/1.0 (ios; iPhone)"},
    )
    assert_true(ios_upgrade.status_code == 403, "iOS upgrade endpoint is blocked")

    print("PASS: subscription billing wiring audit passed")


if __name__ == "__main__":
    try:
        run()
    finally:
        try:
            Path(tmp_db.name).unlink(missing_ok=True)
        except Exception:
            pass
