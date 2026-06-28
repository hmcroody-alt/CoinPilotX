# Economy Finance Security Review

## Security Model

The Economy layer is a read-safe operating layer over existing finance systems. It does not move funds, credit wallets, modify subscriptions, refund payments, or change payout state.

## Protected Data

The implementation avoids rendering:

- Raw card data
- Bank details
- Stripe customer IDs
- Stripe subscription IDs
- Payment method IDs
- Provider tokens
- Private keys
- Database URLs
- Webhook secrets
- Raw filesystem or storage paths

## Owner Scoping

User-facing Economy data is built from the current authenticated user ID. Summaries are owner-scoped and aggregate-only.

## Admin Scoping

Admin pages are permission gated. Finance diagnostics show safe operational totals and route-backed tools. Provider mutations, refunds, payout approvals, and destructive money actions are not added in this mission.

## Fraud and Risk Signals

The Economy Hub includes fraud risk, payment health, trust score, disputes, payment failures, chargebacks, refund queue, payout readiness, and tax status. These are advisory signals from available backend tables and missing-table-safe fallbacks.

## Platform Compliance

The Premium and Subscriptions subsystems preserve the native iOS paid-digital restriction boundary. The Economy UI does not expose Stripe identifiers or native iOS paid purchase controls.

## Remaining Risks

- Some finance source tables may be absent in older environments; the service handles this by returning safe zero/review states.
- Payout, refund, tax, chargeback, and provider mutation workflows remain routed to existing protected admin systems instead of being newly created here.
