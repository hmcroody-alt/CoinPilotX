# PulseSoc Advertiser Portal Report

## Delivered Portal Areas

- Overview metrics for accounts, campaigns, active campaigns, pending reviews, total spend, wallet balance, and reserved budget.
- Account Center with business profile and masked tax identifier handling.
- Campaign Wizard with objective, campaign info, budget, schedule, placements, and draft save.
- Creative Studio for text, image, video, audio, and hologram-ready creatives.
- Placements inventory view sourced from placement metadata.
- Budget Manager with pause/resume/duplicate/archive controls.
- Analytics with impressions, clicks, CTR, spend, estimated CPC, and estimated CPM.
- Wallet and Billing Center with safe transactions and receipt summaries.
- Review Status and Notifications for advertiser-owned accounts.

## Security

- All advertiser data is scoped by account ownership or account team role.
- Writes require CSRF.
- Destination and media URLs are validated server-side.
- Provider identifiers and secrets are not returned to the advertiser UI.
- Creatives cannot serve until admin/moderator review approves them.

## Notes

- The portal uses existing `/pulse/advertise` and `/pulse/ads` routes.
- Funding controls show prepared states unless server billing is enabled.
