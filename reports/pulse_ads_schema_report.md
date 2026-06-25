# PulseSoc Ads Schema Report

Date: 2026-06-24

## Tables

- `pulse_ad_accounts`: advertiser ownership and business verification state.
- `pulse_ad_campaigns`: campaign objective, status, budget shell, start/end window.
- `pulse_ad_creatives`: ad creative content, destination URL, moderation state.
- `pulse_ad_placements`: approved platform placements and frequency defaults.
- `pulse_ad_campaign_placements`: campaign-to-placement join table.
- `pulse_ad_targeting`: broad contextual targeting only.
- `pulse_ad_impressions`: rendered impression and viewability state.
- `pulse_ad_clicks`: click events and destination URL snapshot.
- `pulse_ad_events`: secondary ad events such as hide/report/save.
- `pulse_ad_frequency_caps`: per-user/session campaign placement caps.
- `pulse_ad_moderation_queue`: review queue.
- `pulse_ad_policy_flags`: automated policy findings.
- `pulse_ad_audit_logs`: admin/advertiser action trail.
- `pulse_ad_review_board`: admin moderation board state.
- `pulse_ad_platform_settings`: platform kill switch.

## Seeded Placements

- `feed_inline`
- `feed_side_ufo_desktop`
- `feed_inline_ufo_mobile`
- `pulse_network_hologram`
- `creator_sidebar_signal`
- `marketplace_sponsor`
- `pulse_radio_sponsor`
- `video_pre_roll`
- `status_interstitial`
- `search_sponsored_result`
- `dashboard_sponsor`
- `profile_sponsor`

## Compatibility

- Tables are additive and do not delete or rename existing data.
- Indexes are created through `safe_create_index`.
- New auto-increment tables were added to the database compatibility registry for PostgreSQL `RETURNING` support.
