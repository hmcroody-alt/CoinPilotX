# PulseSoc Advertiser Portal Audit

## Scope

Phase 3 adds an advertiser-facing Mission Control portal on top of the existing PulseSoc Ads Foundation and Delivery Engine. It does not replace ad delivery, admin review, tracking, moderation, or placement selection.

## Current Ads Foundation

- Advertiser-owned account, campaign, creative, analytics, and creative-submission APIs already existed under `/api/pulse/ads/*`.
- Admin review and moderation APIs remain under `/api/admin/pulse/ads/*`.
- Delivery uses approved creatives, active campaigns, placement metadata, frequency caps, signed delivery tokens, and privacy-safe tracking.
- Ads delivery remains controlled by the existing kill switch and moderation flow.

## New Portal Surface

- Page routes:
  - `/pulse/advertise`
  - `/pulse/ads`
- API routes:
  - `GET /api/pulse/ads/portal`
  - `GET|POST /api/pulse/ads/accounts/<account_id>/profile`
  - `GET /api/pulse/ads/accounts/<account_id>/billing-summary`
  - `GET|PATCH /api/pulse/ads/campaigns/<campaign_id>`
  - `POST /api/pulse/ads/campaigns/<campaign_id>/action`
  - `POST /api/pulse/ads/creatives/<creative_id>/action`
  - `POST /api/pulse/ads/creatives/<creative_id>/replace`

## Database Additions

Additive tables:

- `pulse_ad_account_profiles`
- `pulse_ad_team_members`
- `pulse_ad_billing_profiles`
- `pulse_ad_notifications`
- `pulse_ad_campaign_history`

Additive columns:

- `pulse_ad_campaigns.archived_at`
- `pulse_ad_campaigns.submitted_at`
- `pulse_ad_campaigns.approved_at`
- `pulse_ad_campaigns.completed_at`
- `pulse_ad_creatives.archived_at`
- `pulse_ad_creatives.metadata_json`
- `pulse_ad_creatives.compatibility_json`
- `pulse_ad_creatives.moderation_history_json`

No existing ad tables are dropped or rewritten.

## Security Model

- All portal routes require authenticated PulseSoc account access.
- Account data is scoped by advertiser ownership or active team role.
- Campaign and creative mutations require owner, campaign manager, or marketing manager role.
- Analytics access is owner/team scoped only.
- Billing summary intentionally excludes Stripe customer IDs and live payment provider identifiers.
- Tax identifiers are stored and returned masked only.
- Admin review remains admin-only; advertisers can view their own review status but cannot approve ads.
- All write APIs require CSRF headers.
- Destination and media URLs continue through the existing ads service URL validation.

## Portal Capabilities

- Advertiser Account Center:
  - create first ad account
  - business profile
  - verification state
  - health score
  - spend summary
  - notification summary
- Campaign Wizard:
  - objective
  - name
  - placements
  - budget type
  - daily/lifetime budget
  - schedule fields
  - draft saving
- Creative Studio:
  - text/image/video/audio/hologram creative records
  - media URL and thumbnail URL metadata
  - submit for review
  - duplicate
  - archive
  - delete draft only
- Budget Manager:
  - budget display
  - remaining budget
  - pause/resume/duplicate/archive actions
  - billing-prep state with no live charging
- Analytics:
  - impressions
  - viewable impressions
  - clicks
  - CTR
  - hides
  - reports
- Review Board:
  - own submitted creatives
  - review status
  - rejection reason
  - risk score

## Validation

Run:

```bash
venv/bin/python -m py_compile bot.py services/pulse_ads_service.py services/pulse_advertiser_portal.py scripts/pulse_advertiser_portal_audit.py
venv/bin/python scripts/pulse_ads_foundation_audit.py
venv/bin/python scripts/pulse_ads_delivery_engine_audit.py
venv/bin/python scripts/pulse_advertiser_portal_audit.py
git diff --check
```

## Remaining Risks

- Direct file upload storage is still future work; current portal safely supports approved media URLs and metadata replacement.
- Team invitations are prepared at the database and permission layer, but invite email flows are not activated.
- Live billing, auto recharge, and payment collection are intentionally feature-flagged and disabled by default.
