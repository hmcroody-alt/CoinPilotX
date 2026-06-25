# PulseSoc Ads Foundation Audit

Date: 2026-06-24

## Scope

Built an additive PulseSoc ads foundation for campaign lifecycle, moderation, approved-only serving, privacy-safe tracking, frequency caps, and admin controls.

## Existing State

- Existing `ad_placements`, `ad_reports`, `sponsor_slots`, and `ad_reviews` were lightweight sponsor/review placeholders.
- They did not provide complete advertiser accounts, campaign placement joins, moderation queue, policy flags, impressions, clicks, viewability, frequency caps, audit logs, or a platform kill switch.
- The new implementation leaves those tables intact and adds isolated `pulse_ad_*` tables.

## Files Added Or Changed

- `bot.py`
- `services/db.py`
- `services/pulse_ads_service.py`
- `static/js/pulse_ads_hooks.js`
- `scripts/pulse_ads_foundation_audit.py`
- `reports/pulse_ads_*.md`

## Validation

Passed:

- `venv/bin/python -m py_compile bot.py services/pulse_ads_service.py services/db.py scripts/pulse_ads_foundation_audit.py`
- `venv/bin/python scripts/pulse_ads_foundation_audit.py`

Audit coverage:

- Required `pulse_ad_*` tables exist.
- Required placements seed correctly.
- Unapproved creative does not serve.
- Approved creative only serves when campaign is active.
- Paused campaign does not serve.
- Kill switch blocks ad serving.
- Unsafe destination URL is rejected.
- Impression, viewability, click, and hide tracking persist.
- Client ad payload does not expose targeting or advertiser private fields.
- Admin review endpoint rejects anonymous access.
- Tracking write endpoint rejects missing CSRF.

## Remaining Risks

- No visible ad slots were force-injected into the feed in this task. The frontend hook is ready, but exact placement rollout should be done with browser QA per surface.
- Billing, bidding, auction pacing, advertiser self-serve UI, and payment collection are intentionally not included.
