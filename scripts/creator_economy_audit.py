#!/usr/bin/env python3
"""Creator economy foundation audit.

This checks the boring things that matter in money systems: tables, routes,
fees, Stripe setup visibility, entitlements, webhook idempotency, and ledger
balance reconciliation.
"""

from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import bot  # noqa: E402
from services import creator_economy_service, payment_provider, premium_entitlement_service  # noqa: E402


REQUIRED_TABLES = [
    "creator_wallets",
    "creator_ledger_entries",
    "seller_payout_accounts",
    "creator_transactions",
    "creator_payouts",
    "platform_fee_rules",
    "payment_audit_logs",
    "premium_entitlements",
    "subscriptions",
    "payment_webhook_events",
    "platform_wallets",
    "treasury_transactions",
    "creator_balances",
    "payout_queue",
    "payout_history",
    "payout_failures",
    "settlement_batches",
    "creator_tax_profiles",
    "revenue_breakdown",
    "fee_ledger",
    "escrow_holds",
    "platform_payouts",
]

REQUIRED_ROUTES = [
    "/api/payments/checkout/product/<int:product_id>",
    "/api/payments/checkout/course/<int:course_id>",
    "/api/payments/checkout/live-class/<int:class_id>",
    "/api/payments/checkout/premium/<plan_key>",
    "/payments/success",
    "/payments/cancel",
    "/webhooks/stripe",
    "/pulse/merchant/payouts",
    "/pulse/teacher/payouts",
    "/pulse/creator/payouts",
    "/admin/payments",
    "/admin/treasury",
    "/admin/platform-revenue",
    "/admin/creator-payouts",
    "/admin/fee-ledger",
    "/admin/escrow",
    "/admin/settlements",
    "/admin/stripe-connect",
    "/admin/tax-center",
    "/admin/revenue-analytics",
    "/admin/financial-audit",
    "/admin/refunds",
    "/admin/disputes",
    "/legal/payments",
    "/legal/refunds",
    "/legal/seller-terms",
]


def table_exists(cur, table: str) -> bool:
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,))
    return bool(cur.fetchone())


def scalar(cur, sql: str, params=()):
    cur.execute(sql, params)
    row = cur.fetchone()
    if not row:
        return 0
    try:
        return list(dict(row).values())[0]
    except Exception:
        return row[0]


def main() -> int:
    failures: list[str] = []
    warnings: list[str] = []
    bot.init_db()
    conn = bot.db()
    conn.row_factory = bot.sqlite3.Row
    cur = conn.cursor()

    for table in REQUIRED_TABLES:
        if not table_exists(cur, table):
            failures.append(f"missing table: {table}")

    for seller_type, item_type in [
        ("merchant", "product"),
        ("teacher", "course"),
        ("teacher", "lesson"),
        ("teacher", "live_class"),
        ("platform", "premium"),
    ]:
        try:
            rule = creator_economy_service.get_fee_rule(seller_type, item_type)
            if rule.get("fee_percent") is None:
                failures.append(f"missing fee rule: {seller_type}/{item_type}")
        except Exception as exc:
            failures.append(f"fee rule error {seller_type}/{item_type}: {exc}")

    route_rules = {str(rule.rule) for rule in bot.webhook_app.url_map.iter_rules()}
    for route in REQUIRED_ROUTES:
        if route not in route_rules:
            failures.append(f"missing route: {route}")

    provider = payment_provider.provider_status()
    if not provider.get("secret_key_loaded"):
        warnings.append("STRIPE_SECRET_KEY missing: checkout stays setup-gated")
    if not provider.get("webhook_secret_loaded"):
        warnings.append("STRIPE_WEBHOOK_SECRET missing: live webhook processing stays setup-gated")

    try:
        grant = premium_entitlement_service.grant_entitlement(0, "audit_probe", source="audit", metadata={"audit": True})
        if not grant.get("ok"):
            failures.append("premium entitlement grant failed")
        if not premium_entitlement_service.has_entitlement(0, "audit_probe"):
            failures.append("premium entitlement lookup failed")
        premium_entitlement_service.revoke_entitlement(0, "audit_probe", "audit cleanup")
    except Exception as exc:
        failures.append(f"entitlement service error: {exc}")

    negative = scalar(cur, "SELECT COUNT(*) AS total FROM creator_wallets WHERE available_balance_cents < 0 OR pending_balance_cents < 0")
    if negative:
        failures.append(f"negative wallet balances: {negative}")

    cur.execute("SELECT * FROM creator_wallets LIMIT 100")
    for row in cur.fetchall():
        wallet = dict(row)
        result = creator_economy_service.reconcile_wallet(int(wallet["id"]))
        if not result.get("ok"):
            failures.append(f"wallet reconcile failed: {wallet['id']}")

    duplicate_events = scalar(cur, "SELECT COUNT(*) AS total FROM (SELECT provider_event_id FROM payment_webhook_events WHERE provider_event_id!='' GROUP BY provider_event_id HAVING COUNT(*) > 1)")
    if duplicate_events:
        failures.append(f"duplicate webhook events: {duplicate_events}")

    conn.close()
    print("CREATOR ECONOMY AUDIT")
    status_conn = bot.db()
    status_cur = status_conn.cursor()
    for table in REQUIRED_TABLES:
        print(f"table {table}: {'ok' if table_exists(status_cur, table) else 'missing'}")
    status_conn.close()
    print(f"provider mode: {provider.get('mode')}")
    print(f"routes checked: {len(REQUIRED_ROUTES)}")
    print(f"warnings: {len(warnings)}")
    for warning in warnings:
        print(f"WARN {warning}")
    print(f"failures: {len(failures)}")
    for failure in failures:
        print(f"FAIL {failure}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
