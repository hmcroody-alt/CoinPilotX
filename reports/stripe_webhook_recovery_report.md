# Stripe Webhook Recovery Report

Date: 2026-06-09

## Canonical Endpoint

Keep this Stripe Dashboard endpoint active:

- `https://pulsesoc.com/api/stripe/webhook`

The same backend also accepts these aliases for compatibility:

- `https://coinpilotx.app/stripe/webhook`
- `https://coinpilotx.app/api/stripe/webhook`
- `https://pulsesoc.com/stripe/webhook`
- `https://pulsesoc.com/api/stripe/webhook`
- `/stripe-webhook`
- `/webhook/stripe`
- `/webhooks/stripe`

## Implemented Recovery

- Verified webhook handler uses `request.data` raw body before JSON parsing.
- Verified signature verification uses `stripe.Webhook.construct_event`.
- Added safe audit script: `scripts/stripe_webhook_recovery_audit.py`.
- Confirmed idempotency through `payment_webhook_events` and legacy `stripe_events`.
- Added explicit `payment_intent.payment_failed` handling.
- Existing handler covers checkout, invoices, subscriptions, payment intents, refunds, payouts, and disputes.

## Required Stripe Events

Configured code coverage exists for:

- `checkout.session.completed`
- `invoice.paid`
- `invoice.payment_failed`
- `customer.subscription.created`
- `customer.subscription.updated`
- `customer.subscription.deleted`
- `payment_intent.succeeded`
- `payment_intent.payment_failed`
- `charge.refunded`
- `payout.paid`
- `payout.failed`

## Dashboard Cleanup

If Stripe allows only one endpoint for this product, remove duplicate Dashboard endpoints after confirming `https://pulsesoc.com/api/stripe/webhook` has successful live deliveries. Keep `coinpilotx.app` aliases only if legacy checkout sessions still reference that host.

## Production Gate

Railway must have live values for:

- `STRIPE_SECRET_KEY`
- `STRIPE_WEBHOOK_SECRET`
- Founder/Premium/Marketplace/Teacher price IDs

Secrets were not printed or committed.

