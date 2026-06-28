# Ads & Sponsorships Privacy and Security Review

Date: 2026-06-28

## Security Boundary

The Ads & Sponsorships command layer exposes commercial summaries only. It does not expose:

- raw push or provider tokens
- internal secrets
- database URLs
- storage object keys or filesystem paths
- raw targeting records
- raw sessions
- checkout secrets
- provider credential material
- cross-advertiser private data

## Authorization

User-facing state is scoped to the authenticated user's advertiser account ownership.

Admin Ads Command Center routes require protected admin access through `require_admin_page("command_center.view")`. Non-admin sessions are rejected or redirected.

## Moderation and Delivery Safety

The UI and state model preserve the existing ads delivery rules:

- Ads require approved creatives.
- Campaigns must be active and eligible.
- Placements must be active.
- Delivery remains frequency-cap and privacy-bound.
- Review, moderation, audit, and delivery controls remain server-side.

## State Labels

The command center uses strict state labels:

- READY
- ACTION REQUIRED
- REVIEW
- WARNING
- LOCKED
- PREMIUM
- ADMIN
- PARTIAL
- BETA
- COMING SOON

No card relies on a fake generic active state.

## Privacy Notes

Advertiser analytics are aggregate-only. Viewer identity, hidden targeting criteria, and private user data are not rendered in the user dashboard.

## Legal Name Correction

Touched admin surfaces continue using the corrected legal display name `CoinPlotXAI Inc.` where visible in shared admin shell output.
