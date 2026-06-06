# Stripe Founder Checkout

Date: 2026-06-06

## Summary

Pulse Founder Premium checkout is wired through Stripe Checkout subscriptions. Clicking the Founder CTA creates a hosted Stripe Checkout Session, but Founder access is not granted from the click or success page. Access is granted or revoked only after a verified Stripe webhook reaches the app.

## Implemented

- Added `POST /api/premium/checkout` for Founder Premium subscription checkout.
- Added `POST /api/premium/billing-portal` for Stripe-hosted billing management.
- Added `/api/stripe/webhook` as an alias for the existing verified Stripe webhook.
- Added `/pulse/premium/success` and `/pulse/premium/cancel`.
- Updated `/pulse/premium` and `/pulse/premium/activate` to show real checkout/billing actions.
- Preserved manual admin Founder grant and revoke flows.
- Added safe fallback copy when Stripe config is missing: “Founder checkout is being connected. Admin Founder access is available now.”
- Added Founder Stripe persistence fields for customer, subscription, checkout session, price, product, provider status, billing period, cancellation state, and webhook payload audit.
- Added `scripts/stripe_premium_audit.py`.

## Access Rules

Checkout creation does not grant Founder access.

Founder access is granted only after verified Stripe events:

- `checkout.session.completed`
- `customer.subscription.created`
- `customer.subscription.updated`
- `invoice.payment_succeeded`

Founder access is revoked after verified subscription cancellation/deletion events:

- `customer.subscription.deleted`
- terminal inactive subscription states such as `canceled`, `incomplete_expired`, or `unpaid`

Payment failure is recorded as `past_due` for the Founder subscription record so the account can be followed up without guessing.

## Required Railway Environment

Set these without exposing values in logs or reports:

- `STRIPE_SECRET_KEY`
- `STRIPE_WEBHOOK_SECRET`
- `STRIPE_FOUNDER_PRICE_ID`
- `STRIPE_PREMIUM_PRICE_ID`
- `STRIPE_PREMIUM_PLUS_PRICE_ID`
- `NEXT_PUBLIC_STRIPE_PUBLISHABLE_KEY`
- `PAYMENT_PROVIDER_ENABLED=true`
- `PULSE_APP_URL=https://pulsesoc.com`

## Stripe Dashboard Setup

Status: not completed from this coding pass. The dashboard currently requires manual product/price/webhook setup and secret storage in Railway without exposing secrets in chat or reports.

Create or verify:

- Product: Pulse Founder Premium
- Recurring monthly price: `$4.99`
- Save the price ID into `STRIPE_FOUNDER_PRICE_ID`
- Webhook endpoint: `https://pulsesoc.com/api/stripe/webhook`
- Events:
  - `checkout.session.completed`
  - `customer.subscription.created`
  - `customer.subscription.updated`
  - `customer.subscription.deleted`
  - `invoice.payment_succeeded`
  - `invoice.payment_failed`

Do not paste webhook signing secrets into chat or reports. Store the signing secret only in Railway as `STRIPE_WEBHOOK_SECRET`.

## Webhook Events Configured

Target endpoint for Stripe Dashboard:

- `https://pulsesoc.com/api/stripe/webhook`

Required events:

- `checkout.session.completed`
- `customer.subscription.created`
- `customer.subscription.updated`
- `customer.subscription.deleted`
- `invoice.payment_succeeded`
- `invoice.payment_failed`

## Test Payment Status

No live or test payment was completed in this local pass because the Stripe product price ID and webhook signing secret must be configured in the deployment environment first.

## Entitlement Activation Result

Local code path is ready:

- Checkout creates a Stripe-hosted subscription session.
- Success URL displays a pending activation message only.
- Verified webhook activates Founder membership, assigns Founder number once, stores Stripe subscription fields, and records payment/audit state.
- Verified subscription deletion or terminal inactive state revokes active paid Premium/Founder access while preserving admin manual grant capability.

## Known Issues

- Stripe Dashboard product/price/webhook setup still needs manual completion.
- Railway must be updated with the required env vars before live checkout can be tested.
- Customer Portal requires a Stripe customer ID, so it appears only after a checkout/customer profile exists.

## Validation Notes

The implementation follows Stripe’s requirement that webhook signature verification uses the raw request body and the `Stripe-Signature` header. Billing management is delegated to Stripe’s hosted Customer Portal.

## Remaining Live QA

Live Stripe QA requires configured Railway environment values and a Stripe test/live product price:

- Open `/pulse/premium`.
- Click Activate Founder Membership.
- Complete Stripe Checkout.
- Confirm webhook delivery in Stripe Dashboard.
- Confirm Founder number appears after webhook processing.
- Open Manage Billing and verify Stripe Customer Portal loads.
- Cancel subscription in Stripe test mode and confirm Founder access is revoked after webhook.
