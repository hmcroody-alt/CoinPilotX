#!/usr/bin/env python3
"""Audit Stripe Founder Premium checkout and verified webhook wiring."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BOT = (ROOT / "bot.py").read_text(encoding="utf-8")
SERVICE = (ROOT / "services" / "premium_entitlement_service.py").read_text(encoding="utf-8")


def expect(condition, label):
    if not condition:
        raise AssertionError(label)
    print(f"ok - {label}")


def main():
    required_bot_tokens = [
        'STRIPE_FOUNDER_PRICE_ID',
        'STRIPE_FOUNDER_PRODUCT_ID',
        'NEXT_PUBLIC_STRIPE_PUBLISHABLE_KEY',
        'PAYMENT_PROVIDER_ENABLED',
        'PULSE_APP_URL',
        '@webhook_app.route("/api/premium/checkout", methods=["POST"])',
        '@webhook_app.route("/api/premium/billing-portal", methods=["POST"])',
        '@webhook_app.route("/api/premium/status", methods=["GET"])',
        '@webhook_app.route("/api/stripe/webhook", methods=["POST"])',
        'stripe.Webhook.construct_event',
        'request.data',
        'stripe.checkout.Session.create',
        '"mode": "subscription"',
        '"plan": "founder_premium"',
        '"company": "CoinPilotXAI Inc."',
        '"environment": stripe_environment_label()',
        '"price_id": STRIPE_FOUNDER_PRICE_ID',
        'stripe.billing_portal.Session.create',
        'This page never grants access from the URL alone.',
        '/api/premium/status',
        'Checkout canceled. You can activate Founder Premium anytime.',
        'Founder checkout is being connected. Admin Founder access is available now.',
        'notify_payment_status',
        'activate_founder_from_stripe_session',
        'sync_founder_subscription_from_stripe',
        'sync_founder_invoice_from_stripe',
        'premium_entitlement_service.grant_founder_membership',
        'premium_entitlement_service.revoke_premium_access',
    ]
    for token in required_bot_tokens:
        expect(token in BOT, f"bot.py contains {token}")

    required_service_tokens = [
        'stripe_customer_id',
        'stripe_subscription_id',
        'stripe_checkout_session_id',
        'stripe_price_id',
        'stripe_product_id',
        'provider_status',
        'current_period_start',
        'current_period_end',
        'cancel_at_period_end',
        'update_founder_stripe_subscription',
        'CREATE TABLE IF NOT EXISTS stripe_events',
    ]
    for token in required_service_tokens:
        expect(token in SERVICE, f"premium entitlement service contains {token}")

    checkout_index = BOT.index('@webhook_app.route("/api/premium/checkout", methods=["POST"])')
    webhook_index = BOT.index('def stripe_webhook():')
    grant_index = BOT.index('def activate_founder_from_stripe_session')
    expect(grant_index > checkout_index, "Founder grant helper is separate from checkout creation")
    expect(webhook_index > grant_index, "Founder webhook helpers are available before webhook dispatch")
    expect('premium_entitlement_service.grant_founder_membership(user_id, 0, "stripe_checkout")' in BOT, "Founder grants come from webhook-confirmed checkout helper")

    print("STRIPE_PREMIUM_AUDIT_PASS")


if __name__ == "__main__":
    main()
