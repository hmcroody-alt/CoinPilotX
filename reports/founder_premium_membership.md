# Founder Premium Membership

## Plans

- Free: `$0/month`, normal Pulse access.
- Founder Premium: `$4.99/month`, lifetime locked Founder pricing with `$9.99/month` regular equivalent value.
- Premium Plus: `$9.99/month`, marked coming soon.

## Backend Foundation

Founder Premium now has plan, subscription, entitlement, Founder membership, badge, and Founder Wall tables:

- `subscription_plans`
- `user_subscriptions`
- `user_entitlements`
- `founder_memberships`
- `premium_badges`
- `founder_wall_entries`

Founder numbers are unique and reused on repeat grants. Founder access is granted through backend helpers and admin controls, not through frontend state.

## Entitlements

Tracked Founder entitlements include:

- `premium_access`
- `founder_access`
- `founder_badge`
- `founder_hub_access`
- `creator_analytics`
- `creator_studio_pro`
- `ai_creator_assistant`
- `priority_support`
- `priority_verification`
- `premium_profile_themes`
- `premium_upload_limits`
- `early_access_features`

## Admin Activation

Public checkout remains disabled until payment automation is ready. Admins can grant or revoke Founder Premium from Premium Command. Regular users cannot grant themselves Founder access.

## Payment Prep

The system reads safe payment placeholders without requiring Stripe to be configured:

- `PAYMENT_PROVIDER_ENABLED`
- `STRIPE_FOUNDER_PRICE_ID`
- `STRIPE_PREMIUM_PRICE_ID`
- `STRIPE_PREMIUM_PLUS_PRICE_ID`

Missing Stripe keys do not break Premium or the app.
