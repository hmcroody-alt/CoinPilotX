# PulseSoc Ad Finance Security Review

## Controls

- Money values are stored as integer cents with explicit currency.
- Idempotency keys are required for wallet funding and spend mutations.
- Stripe webhook signature verification remains handled by the existing webhook entrypoint.
- The advertiser wallet processor only accepts Stripe sessions with PulseSoc ad wallet metadata.
- Raw card data is never stored.
- Stripe IDs are not returned from wallet, billing, or portal summaries.
- CSRF is required for advertiser and admin writes.
- Rate limiting protects funding and tracking endpoints.
- Owner scoping prevents another user from reading or mutating advertiser accounts.
- Admin finance view is admin-only.

## Risk Notes

- Refund execution is prepared by schema but should be implemented with explicit admin workflow before public use.
- Live funding should remain disabled until production Stripe test and accounting review pass.
- iOS native advertiser billing remains blocked until policy review confirms business ad spend handling.
