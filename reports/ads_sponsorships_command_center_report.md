# Ads & Sponsorships Command Center Report

Date: 2026-06-28

## Summary

The Dashboard Ads & Sponsorships section is now backed by a real commercial command layer instead of static feature cards. User-facing cards route to real dashboard subsystems, use contextual actions, and derive state from advertiser-owned campaign, creative, wallet, placement, impression, click, and conversion data where available.

The internal LogiNexus design philosophy was applied as invisible product behavior only. The term is not exposed in user-facing UI.

## User Surfaces

- `/dashboard/ads`
- `/dashboard/advertising`
- `/dashboard/ads/sponsored-signals`
- `/dashboard/ads/manager`
- `/dashboard/ads/campaign-builder`
- `/dashboard/ads/signal-studio`
- `/dashboard/ads/analytics`
- `/dashboard/ads/brand-deals`
- `/dashboard/ads/creator-sponsorships`
- `/dashboard/ads/revenue-intelligence`
- `/dashboard/ads/audience-targeting`
- `/dashboard/ads/conversion-tracking`
- `/api/dashboard/ads/state`

## Backend/Admin Surfaces

- `/admin/ads-command-center`
- `/admin/ads-command-center/<section_key>`
- Links into protected existing admin tools:
  - Ads Review Board
  - Ads registry
  - Pulse analytics
  - Payment command center
  - Moderation
  - Creator command center
  - Economy command center
  - Audit logs
  - System health

## Subsystems

- Sponsored Signal Intelligence
- Commercial Mission Control
- Campaign Builder
- Sponsored Signal Studio
- Ad Analytics
- Brand Deals
- Creator Sponsorships
- Revenue Intelligence
- Audience Targeting
- Conversion Tracking

## Data Sources

The command state reads only safe aggregates from existing PulseSoc ads infrastructure tables when present:

- `pulse_ad_accounts`
- `pulse_ad_campaigns`
- `pulse_ad_creatives`
- `pulse_ad_media_assets`
- `pulse_ad_placements`
- `pulse_ad_targeting`
- `pulse_ad_impressions`
- `pulse_ad_clicks`
- `pulse_ad_events`
- `pulse_ad_moderation_queue`
- `pulse_ad_policy_flags`
- `pulse_ad_audit_logs`
- `pulse_ad_wallets`

## Remaining Work

Brand deals and creator sponsorships remain real routed subsystems with beta state because deeper deal-contract workflow tables are still future-ready. The visible routes are not placeholders: they show current commercial readiness, recommendations, and protected routing into the existing ads portal/admin systems.
