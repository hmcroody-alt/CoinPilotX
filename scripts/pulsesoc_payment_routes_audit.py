#!/usr/bin/env python3
"""Audit PulseSoc payment routes without contacting Stripe."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BOT_SOURCE = (ROOT / "bot.py").read_text(encoding="utf-8")


def expect(condition: bool, label: str, detail: str = "") -> None:
    if not condition:
        raise AssertionError(f"{label}: {detail}")
    print(f"ok - {label}")


def static_checks() -> None:
    required = [
        '@webhook_app.route("/api/premium/checkout", methods=["POST"])',
        '@webhook_app.route("/api/premium/billing-portal", methods=["POST"])',
        '@webhook_app.route("/api/premium/status", methods=["GET"])',
        '@webhook_app.route("/pulse/premium/success", methods=["GET"])',
        '@webhook_app.route("/pulse/premium/cancel", methods=["GET"])',
        '@webhook_app.route("/billing/portal", methods=["GET"])',
        'stripe.Webhook.construct_event',
        'stripe_event_processed(event_id)',
        'notify_payment_status',
        'CoinPilotXAI Inc.',
        'stripe_environment_label()',
        '"price_id": STRIPE_FOUNDER_PRICE_ID',
        '"product_id": STRIPE_FOUNDER_PRODUCT_ID or STRIPE_PRODUCT_ID',
        'This page never grants access from the URL alone',
        "Retry Founder Checkout",
        "Billing portal unavailable.",
        "/dashboard/economy/subscriptions",
    ]
    for token in required:
        expect(token in BOT_SOURCE, f"payment wiring token present {token}")


def table_columns(cur: sqlite3.Cursor, table: str) -> set[str]:
    cur.execute(f"PRAGMA table_info({table})")
    return {row[1] for row in cur.fetchall()}


def insert_user(cur: sqlite3.Cursor, user_id: int) -> None:
    now = datetime.now(timezone.utc).isoformat()
    cols = table_columns(cur, "users")
    values = {
        "user_id": user_id,
        "username": "payment_route_audit",
        "display_name": "Payment Route Audit",
        "full_name": "Payment Route Audit",
        "email": "payment-route-audit@example.com",
        "password_hash": "x",
        "signup_time": now,
        "created_at": now,
        "account_status": "active",
        "email_verified": 1,
    }
    data = {key: value for key, value in values.items() if key in cols}
    cur.execute(f"INSERT INTO users ({', '.join(data)}) VALUES ({', '.join(['?'] * len(data))})", tuple(data.values()))


def dynamic_checks() -> None:
    import sys

    sys.path.insert(0, str(ROOT))
    import bot  # noqa: WPS433,E402

    user_id = 9926072401
    bot.init_db()
    conn = bot.db()
    cur = conn.cursor()
    cur.execute("DELETE FROM users WHERE user_id=?", (user_id,))
    insert_user(cur, user_id)
    conn.commit()
    conn.close()

    original_configured = bot.founder_checkout_configured
    original_checkout = bot.create_founder_checkout_session
    original_portal = bot.create_founder_billing_portal_session
    bot.founder_checkout_configured = lambda: True
    bot.create_founder_checkout_session = lambda user: ({"id": "cs_audit", "url": "https://checkout.stripe.com/audit"}, "")
    bot.create_founder_billing_portal_session = lambda user: ({"url": "https://billing.stripe.com/audit"}, "")
    try:
        client = bot.webhook_app.test_client()
        with client.session_transaction() as sess:
            sess["account_user_id"] = user_id
            sess["user_id"] = user_id
        for path in ["/pulse/premium", "/pulse/premium/success?session_id=cs_audit", "/pulse/premium/cancel", "/billing/portal"]:
            response = client.get(path)
            expect(response.status_code in {200, 302, 303}, f"{path} resolves safely", str(response.status_code))
            if path.startswith("/pulse/premium/success"):
                expect(b"/api/premium/status" in response.data, "success page polls backend status")
            if path.startswith("/pulse/premium/cancel"):
                expect(b"data-founder-checkout" in response.data, "cancel page retry checkout button is wired")
        checkout = client.post("/api/premium/checkout", json={"plan_key": "founder_premium"})
        expect(checkout.status_code == 200, "premium checkout API returns JSON success", checkout.get_data(as_text=True)[:300])
        expect(checkout.get_json().get("checkout_url") == "https://checkout.stripe.com/audit", "premium checkout API returns checkout_url")
        portal = client.post("/api/premium/billing-portal", json={})
        expect(portal.status_code == 200, "premium billing portal API returns JSON success", portal.get_data(as_text=True)[:300])
        expect(portal.get_json().get("url") == "https://billing.stripe.com/audit", "premium billing portal API returns url")
        status = client.get("/api/premium/status")
        expect(status.status_code == 200 and status.get_json().get("ok") is True, "premium status API returns backend state")
    finally:
        bot.founder_checkout_configured = original_configured
        bot.create_founder_checkout_session = original_checkout
        bot.create_founder_billing_portal_session = original_portal
        conn = bot.db()
        cur = conn.cursor()
        cur.execute("DELETE FROM users WHERE user_id=?", (user_id,))
        conn.commit()
        conn.close()


def main() -> int:
    static_checks()
    dynamic_checks()
    print("pulsesoc payment routes audit ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
