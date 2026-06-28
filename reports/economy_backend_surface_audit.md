# Economy Backend Surface Audit

## Protected Entry Point

Backend Command Center:

- `/admin/economy-command-center`

Section route:

- `/admin/economy-command-center/<section>`

## Management Sections

- Wallets
- Transactions
- Orders
- Sellers
- Products
- Subscriptions
- Premium
- Payouts
- Revenue
- Affiliate
- Marketplace
- Taxes
- Fraud
- Refunds
- Chargebacks
- Payment Providers
- Stripe
- Apple IAP
- Google Play Billing
- Economy Audit Logs

## Registry

The backend management registry now includes dedicated Economy features for each section. The Economy module blueprint points to `/admin/economy-command-center` and describes safe failure behavior for money actions.

## Link Hygiene

Stale backend inventory routes were replaced with registered routes:

- Ad finance routes now point to `/admin/financial-audit`.
- Monetization routes now point to `/admin/monetization`.
- Premium operations links point to `/admin/premium-command`.

## Permission Boundary

The Economy admin surface requires admin billing permission through `require_admin_page("billing.view")`. Non-admin access is blocked by the audit.
