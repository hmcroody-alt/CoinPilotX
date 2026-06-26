# PulseSoc Ads Delivery Engine Phase 2

## Scope

Phase 2 upgrades the existing PulseSoc Ads Foundation into a production delivery layer. The work is additive: existing ad accounts, campaigns, creatives, review board, moderation queue, audit logs, and platform kill switch remain in place.

## Phase 1 Audit Summary

Existing foundation confirmed:

- `pulse_ad_accounts`, `pulse_ad_campaigns`, `pulse_ad_creatives`
- `pulse_ad_placements`, `pulse_ad_campaign_placements`, `pulse_ad_targeting`
- `pulse_ad_impressions`, `pulse_ad_clicks`, `pulse_ad_events`
- `pulse_ad_frequency_caps`, `pulse_ad_moderation_queue`, `pulse_ad_policy_flags`
- `pulse_ad_audit_logs`, `pulse_ad_review_board`, `pulse_ad_platform_settings`
- Admin review, approve, reject, suspend, and kill-switch routes
- Owner-only advertiser account/campaign/creative routes

## Delivery Engine

Ads now flow through `services/pulse_ads_service.py` with a dedicated delivery model:

- Context-aware placement selection for feed, marketplace, search, radio, dashboard, profile, status, video, and creator contexts
- Device compatibility for desktop, mobile, tablet, and all-device placements
- Active advertiser account gate
- Active campaign gate
- Approved creative/moderation gate
- Placement compatibility by creative type
- Start/end date checks
- Daily/lifetime budget availability checks
- Frequency cap checks
- Recent-campaign penalty to reduce immediate repeated ads
- Weighted scoring using placement priority, campaign priority, and stable rotation

No ad is served if the platform kill switch is disabled.

## Placement Inventory

Public placement metadata is exposed through:

- `GET /api/pulse/ads/placement-metadata`
- `GET /api/pulse/ads/placements`

Metadata is limited to safe public placement details:

- placement key
- display name
- placement type
- device type
- card style
- max frequency
- supported creative types

It does not expose targeting internals, budgets, owner data, private user data, or admin state.

## Tracking Integrity

Served ads now include a signed delivery token and tracking nonce. Tracking endpoints validate that:

- token is present
- token signature is valid
- token has not expired
- creative id, campaign id, and placement match
- viewer/session subject matches
- tracking nonce matches

Impression replay is deduped with `delivery_token_hash` and `request_fingerprint`. The token subject stores a hash, not a readable user id. Tokens do not contain secrets.

## Analytics

Advertiser analytics are available through:

- `GET /api/pulse/ads/analytics`

Returned analytics are aggregate only:

- impressions
- viewable impressions
- clicks
- CTR
- hides
- reports

Viewer ids, session ids, delivery tokens, targeting internals, and private data are not returned.

## Database Changes

Migration-safe additive columns:

- `pulse_ad_campaigns.priority`
- `pulse_ad_campaigns.pacing_mode`
- `pulse_ad_placements.priority`
- `pulse_ad_placements.supported_creative_types`
- `pulse_ad_placements.card_style`
- `pulse_ad_impressions.delivery_token_hash`
- `pulse_ad_impressions.request_fingerprint`
- `pulse_ad_impressions.country`
- `pulse_ad_impressions.language`
- `pulse_ad_impressions.contextual_category`
- `pulse_ad_clicks.delivery_token_hash`
- `pulse_ad_clicks.request_fingerprint`

Indexes added for delivery and tracking lookups.

## Privacy

The delivery engine keeps targeting contextual and aggregate by default:

- broad country only
- language code only
- contextual category only
- no exact location
- no private messages
- no private statuses
- no admin/security data
- no hidden moderation data

Users are treated as opted out of personalized ads unless `privacy_preferences.personalized_ads_opt_out` is explicitly disabled.

## Frontend

`static/js/pulse_ads_hooks.js` now:

- loads contextual placements
- includes viewport data
- carries signed tracking tokens through impressions/clicks/hide/report
- uses server-validated click destination when available
- lazy-loads media
- uses safe DOM text nodes
- avoids unsafe `innerHTML`

Dynamic sponsored cards now have scoped sci-fi styling in `static/css/pulse_home_os.css`.

## Validation

Commands run:

```bash
venv/bin/python -m py_compile bot.py services/pulse_ads_service.py scripts/pulse_ads_foundation_audit.py scripts/pulse_ads_delivery_engine_audit.py
venv/bin/python scripts/pulse_ads_foundation_audit.py
venv/bin/python scripts/pulse_ads_delivery_engine_audit.py
```

Results:

- Compile passed
- Pulse Ads foundation audit passed
- Pulse Ads delivery engine audit passed

## Remaining Risks

- Billing/spend reconciliation is still estimated from delivery activity until a dedicated billing phase connects actual spend.
- Live production QA should verify that approved campaigns are visible in each placement after deployment.
- Fraud controls are basic replay/tamper protections; advanced anomaly detection can be added in a later phase.
