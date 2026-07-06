#!/usr/bin/env python3
"""Validate PulseSoc payment and checkout event emission for native sync."""

from __future__ import annotations

import importlib
import json
import os
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def read(path: str) -> str:
    target = ROOT / path
    if not target.exists():
        raise AssertionError(f"missing required file: {path}")
    return target.read_text(encoding="utf-8")


def require(condition: bool, message: str, failures: list[str]) -> None:
    if not condition:
        failures.append(message)


def import_bot_with_temp_db():
    with tempfile.NamedTemporaryFile(prefix="pulsesoc_payment_events_", suffix=".sqlite", delete=False) as handle:
        db_path = handle.name
    os.environ["DATABASE_URL"] = f"sqlite:///{db_path}"
    os.environ["TELEGRAM_BOT_TOKEN"] = ""
    os.environ["SKIP_TELEGRAM"] = "1"
    os.environ["BREVO_EMAIL_ENABLED"] = "false"
    os.environ["STRIPE_WEBHOOK_SECRET"] = "whsec_audit"
    os.environ.pop("STRIPE_SECRET_KEY", None)
    bot = importlib.import_module("bot")
    bot.STRIPE_SECRET_KEY = ""
    bot.STRIPE_WEBHOOK_SECRET = "whsec_audit"
    bot.stripe.api_key = ""
    bot.stripe.Webhook.construct_event = lambda payload, sig, secret: json.loads(payload.decode("utf-8"))
    if hasattr(bot, "push_service"):
        bot.push_service._async_push_enabled = lambda: False
    if hasattr(bot, "notification_service"):
        bot.notification_service.send_push_alert = lambda *args, **kwargs: {
            "ok": True,
            "status": "skipped",
            "message": "audit stub",
        }
    bot.init_db()
    return bot


def add_user(cur, email: str, username: str, display_name: str, now: str) -> int:
    cur.execute(
        """
        INSERT INTO users (email, username, display_name, password_hash, email_verified, created_at, updated_at)
        VALUES (?, ?, ?, 'x', 1, ?, ?)
        """,
        (email, username, display_name, now, now),
    )
    return int(cur.lastrowid)


def seed_marketplace(bot):
    conn = bot.db()
    conn.row_factory = bot.sqlite3.Row
    cur = conn.cursor()
    now = "2026-07-06T20:00:00"
    buyer_id = add_user(cur, "payment-buyer-qa@example.com", "paymentbuyerqa", "Payment Buyer QA", now)
    seller_id = add_user(cur, "payment-seller-qa@example.com", "paymentsellerqa", "Payment Seller QA", now)
    cur.execute(
        "INSERT INTO marketplace_sellers (user_id, display_name, bio, status, verification_status, created_at, updated_at) VALUES (?, 'Payment Seller QA', 'Payment seller QA account.', 'approved', 'verified', ?, ?)",
        (seller_id, now, now),
    )
    cur.execute(
        """
        INSERT INTO seller_payout_accounts
        (user_id, seller_type, provider, connected_account_id, provider_account_id, onboarding_status, payouts_enabled, charges_enabled, created_at, updated_at)
        VALUES (?, 'merchant', 'stripe', 'acct_payment_audit', 'acct_payment_audit', 'complete', 1, 1, ?, ?)
        """,
        (seller_id, now, now),
    )
    cur.execute(
        """
        INSERT INTO marketplace_listings
        (seller_user_id, title, description, category, price_label, currency, quantity, status, approval_status, created_at, updated_at)
        VALUES (?, 'Payment Event Listing', 'Listing used for payment event audit.', 'Education', '$19.00', 'USD', 5, 'approved', 'approved', ?, ?)
        """,
        (seller_id, now, now),
    )
    listing_id = int(cur.lastrowid)
    conn.commit()
    conn.close()
    return buyer_id, seller_id, listing_id


def checkout(client, listing_id: int, failures: list[str], label: str) -> dict:
    response = client.post("/api/pulse/payments/checkout", json={"item_type": "marketplace_product", "item_id": listing_id})
    data = response.get_json(silent=True) or {}
    require("transaction_id" in data, f"{label} did not expose transaction_id: {data}", failures)
    return data


def post_stripe_event(client, event_id: str, event_type: str, obj: dict, failures: list[str]) -> None:
    payload = {"id": event_id, "type": event_type, "data": {"object": obj}}
    response = client.post(
        "/stripe-webhook",
        data=json.dumps(payload),
        content_type="application/json",
        headers={"Stripe-Signature": "audit-signature"},
    )
    require(response.status_code == 200, f"{event_type} webhook returned HTTP {response.status_code}: {response.get_data(as_text=True)[:200]}", failures)


def run_seeded_payment_flow(failures: list[str]) -> None:
    bot = import_bot_with_temp_db()
    buyer_id, _seller_id, listing_id = seed_marketplace(bot)
    client = bot.webhook_app.test_client()
    with client.session_transaction() as session:
        session["account_user_id"] = buyer_id

    # Blocked checkout: Stripe missing.
    bot.STRIPE_SECRET_KEY = ""
    blocked = checkout(client, listing_id, failures, "blocked checkout")
    blocked_tx = int(blocked.get("transaction_id") or 0)

    # Checkout failure: provider create throws.
    bot.STRIPE_SECRET_KEY = "sk_test_audit"
    bot.stripe.api_key = "sk_test_audit"
    bot.stripe.checkout.Session.create = lambda **kwargs: (_ for _ in ()).throw(RuntimeError("audit checkout failure"))
    failed = checkout(client, listing_id, failures, "failed checkout")
    failed_tx = int(failed.get("transaction_id") or 0)

    # Checkout created: provider returns a safe fake session.
    bot.stripe.checkout.Session.create = lambda **kwargs: {"id": "cs_payment_audit", "url": "https://checkout.stripe.test/audit"}
    created = checkout(client, listing_id, failures, "created checkout")
    tx_id = int(created.get("transaction_id") or 0)
    require(tx_id > 0 and failed_tx > 0 and blocked_tx > 0, "seeded checkout transaction ids were not created", failures)

    metadata = {
        "seller_transaction_id": str(tx_id),
        "buyer_user_id": str(buyer_id),
    }
    post_stripe_event(
        client,
        "evt_payment_checkout_completed",
        "checkout.session.completed",
        {"id": "cs_payment_audit", "payment_status": "paid", "payment_intent": "pi_payment_audit", "metadata": metadata},
        failures,
    )
    post_stripe_event(
        client,
        "evt_payment_checkout_expired",
        "checkout.session.expired",
        {"id": "cs_payment_expired", "metadata": metadata},
        failures,
    )
    post_stripe_event(
        client,
        "evt_payment_intent_failed",
        "payment_intent.payment_failed",
        {"id": "pi_payment_failed", "amount": 1900, "currency": "usd", "metadata": metadata, "last_payment_error": {"code": "card_declined"}},
        failures,
    )
    post_stripe_event(
        client,
        "evt_payment_refunded",
        "charge.refunded",
        {"id": "ch_refunded", "amount": 1900, "amount_refunded": 1900, "metadata": metadata},
        failures,
    )
    post_stripe_event(
        client,
        "evt_payment_dispute_created",
        "charge.dispute.created",
        {"id": "dp_created", "metadata": metadata},
        failures,
    )
    post_stripe_event(
        client,
        "evt_payment_dispute_updated",
        "charge.dispute.updated",
        {"id": "dp_updated", "metadata": metadata},
        failures,
    )
    post_stripe_event(
        client,
        "evt_payment_dispute_closed",
        "charge.dispute.closed",
        {"id": "dp_closed", "metadata": metadata},
        failures,
    )

    sync_response = client.get("/api/pulse/sync/events?limit=100")
    require(sync_response.status_code == 200, f"sync cursor returned HTTP {sync_response.status_code}", failures)
    events = (sync_response.get_json(silent=True) or {}).get("events") or []
    by_type = {event.get("event_type"): event for event in events}
    required = {
        "payment_pending",
        "checkout_blocked",
        "checkout_failed",
        "checkout_created",
        "checkout_expired",
        "payment_succeeded",
        "payment_failed",
        "refund_issued",
        "dispute_opened",
        "dispute_updated",
        "dispute_resolved",
    }
    missing = sorted(required - set(by_type))
    require(not missing, f"sync cursor missing payment events: {missing}", failures)
    for event_type in required:
        event = by_type.get(event_type) or {}
        metadata = event.get("metadata") or {}
        invalidates = set(event.get("invalidate") or metadata.get("invalidates") or [])
        for key in ["event_type", "entity_type", "entity_id", "actor_id", "timestamp", "sync_cursor_key"]:
            require(key in metadata, f"{event_type} metadata missing {key}", failures)
        require(metadata.get("domain") == "commerce", f"{event_type} missing commerce domain", failures)
        require(metadata.get("category") == "payments", f"{event_type} missing payments category", failures)
        for subsystem in ["activity", "notifications", "orders", "seller_inventory", "marketplace"]:
            require(subsystem in invalidates, f"{event_type} does not invalidate {subsystem}", failures)


def main() -> int:
    failures: list[str] = []
    bot_source = read("bot.py")
    report = read("reports/pulsesoc_native_payment_checkout_event_emission.md")
    progress = read("reports/pulsesoc_native_progress.md")

    for token in [
        "def pulse_emit_payment_checkout_event",
        "checkout_created",
        "checkout_blocked",
        "checkout_failed",
        "checkout_expired",
        "payment_pending",
        "payment_succeeded",
        "payment_failed",
        "refund_issued",
        "dispute_opened",
        "dispute_updated",
        "dispute_resolved",
    ]:
        require(token in bot_source, f"bot.py missing payment event token: {token}", failures)

    for token in [
        "Payment/checkout event coverage %",
        "Remaining Silent Mutation Paths",
        "Event Visibility Through Sync Cursor",
        "Activity/Orders/Seller/Marketplace Consistency Impact",
        "ONE Highest-Impact Fix ONLY",
        "Do not focus on Android",
    ]:
        require(token in report, f"payment checkout event report missing token: {token}", failures)
    require("Payment and Checkout Event Emission Hardening" in progress, "progress report missing payment checkout event section", failures)

    run_seeded_payment_flow(failures)

    if failures:
        print("PulseSoc payment checkout event emission audit failed:")
        for failure in failures:
            print(f"- {failure}")
        return 1

    print("PulseSoc payment checkout event emission audit passed.")
    print("- Checkout blocked/failed/created/expired events are cursor-visible.")
    print("- Payment succeeded/failed, refund issued, and dispute state events are cursor-visible.")
    print("- Android-specific QA/tooling remains intentionally out of scope.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
