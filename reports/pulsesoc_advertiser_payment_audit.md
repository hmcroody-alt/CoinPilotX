# PulseSoc Advertiser Payment Audit

## Existing Systems Audited

- Ads Foundation: existing advertiser accounts, campaigns, creatives, placements, moderation queue, delivery tokens, impressions, clicks, events, frequency caps, and audit logs.
- Ads Delivery Engine: approved campaign plus approved creative plus active placement eligibility already existed; budget and wallet checks were added at selection time.
- Sci-Fi Ad Layer: existing delivery components consume `/api/pulse/ads/placements` and track impressions/clicks/events through signed delivery tokens.
- Dashboard/Mission Control: user dashboard and advertiser entry routes already existed; advertiser portal remains at `/pulse/advertise` and `/pulse/ads`.
- Stripe/Premium/Marketplace: existing consumer premium and seller payment flows remain separate from advertiser wallet funding.
- Permissions: advertiser data is owner scoped; admin review APIs use existing admin permission checks.
- CSRF/rate limiting: existing Pulse Ads write guard remains in place; funding and tracking routes also rate-limit sensitive actions.
- iOS payment restrictions: advertiser funding route blocks native iOS requests and keeps consumer Premium/IAP separate.
- DB migrations: additive SQLite/PostgreSQL-safe tables and indexes were added without destructive changes.

## Findings

- The portal had campaign, creative, and moderation workflows but no durable ad wallet ledger.
- Budget controls existed on campaigns but were not linked to a spendable advertiser balance.
- Stripe was available globally for consumer flows, but advertiser funding needed separate metadata, idempotency, and receipt creation.
- Admin review APIs existed, but a dedicated admin review/finance page and finance API were missing.

## Fixes

- Added advertiser wallet, transaction ledger, funding sessions, invoices, receipts, refunds, and billing event tables.
- Added wallet summaries to the advertiser portal.
- Added web-only Stripe funding session endpoint behind `PULSE_ADS_BILLING_ENABLED`.
- Added Stripe webhook crediting for `purpose=pulse_ad_wallet_funding`.
- Added campaign budget reserve and spend posting.
- Added admin finance summary and review-board page.

## Remaining Controls

- Live Stripe checkout only runs when `PULSE_ADS_BILLING_ENABLED=true` and Stripe env is configured.
- Native iOS advertiser funding is blocked pending policy review.
- Refund processing is schema-ready but remains manual/admin future work.
