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

Status: completed for products, prices, webhook destination, and non-secret Railway variables on 2026-06-06.

Configured:

- Product: Pulse Founder Premium
- Product ID: `prod_UeiBmA8SD9btgM`
- Recurring monthly price: `$4.99 USD/month`
- Founder Price ID: ending in `TFniEl`
- Lookup key: `pulse_founder_premium_monthly`
- Optional future product: Pulse Premium at `$9.99 USD/month`
- Optional future product: Pulse Premium Plus at `$19.99 USD/month`
- Webhook endpoint: `https://pulsesoc.com/api/stripe/webhook`
- Webhook destination ID: `we_1TfOwTFP8qvvGWBIyx0agani`
- Events enabled:
  - `checkout.session.completed`
  - `customer.subscription.created`
  - `customer.subscription.updated`
  - `customer.subscription.deleted`
  - `invoice.payment_succeeded`
  - `invoice.payment_failed`

Railway production variables added:

- `STRIPE_FOUNDER_PRICE_ID`
- `STRIPE_PREMIUM_PRICE_ID`
- `STRIPE_PREMIUM_PLUS_PRICE_ID`
- `NEXT_PUBLIC_STRIPE_PUBLISHABLE_KEY`
- `PAYMENT_PROVIDER_ENABLED=true`
- `PULSE_APP_URL=https://pulsesoc.com`

Railway production variables still pending:

- `STRIPE_SECRET_KEY`
- `STRIPE_WEBHOOK_SECRET`

Stripe masks the live secret key and webhook signing secret in the Dashboard. They were not exposed, copied into reports, or committed. Store those values only in Railway.

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

No live checkout payment was completed yet because `STRIPE_SECRET_KEY` and `STRIPE_WEBHOOK_SECRET` are still missing from the production environment. The checkout and webhook code should remain entitlement-safe because the success page does not grant Founder access.

## Entitlement Activation Result

Pending live verification after the two secret Railway variables are set:

- Checkout creates a Stripe-hosted subscription session.
- Success URL displays a pending activation message only.
- Verified webhook activates Founder membership, assigns Founder number once, stores Stripe subscription fields, and records payment/audit state.
- Verified subscription deletion or terminal inactive state revokes active paid Premium/Founder access while preserving admin manual grant capability.

Refreshing the success page is expected not to duplicate Founder numbers because Founder activation is driven by verified webhook processing, not by the success route.

## Billing Portal Result

Pending live verification after the first successful checkout creates a Stripe customer profile. The app already routes active paid members to Stripe's hosted Customer Portal through `POST /api/premium/billing-portal`.

## Known Issues

- Stripe Dashboard product, price, and webhook setup is complete.
- Railway still needs `STRIPE_SECRET_KEY` and `STRIPE_WEBHOOK_SECRET` to be entered without exposing them.
- Full checkout, webhook entitlement, badge, active membership, billing portal, and cancellation QA are blocked until those two secret values are set and the deployment restarts.
- Customer Portal requires a Stripe customer ID, so it appears only after a checkout/customer profile exists.

## Unrelated CSS Inspection

`static/css/pulse_status_system.css` had no current local diff during this pass. There was nothing to classify or commit separately for the homepage feed status empty-space fix.

## Validation Notes

The implementation follows Stripe’s requirement that webhook signature verification uses the raw request body and the `Stripe-Signature` header. Billing management is delegated to Stripe’s hosted Customer Portal.

## Remaining Live QA

Live Stripe QA requires `STRIPE_SECRET_KEY` and `STRIPE_WEBHOOK_SECRET` in Railway:

- Open `/pulse/premium`.
- Click Activate Founder Membership.
- Complete Stripe Checkout.
- Confirm webhook delivery in Stripe Dashboard.
- Confirm Founder number appears after webhook processing.
- Open Manage Billing and verify Stripe Customer Portal loads.
- Cancel subscription in Stripe test mode and confirm Founder access is revoked after webhook.
