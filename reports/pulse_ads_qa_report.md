# PulseSoc Ads QA Report

Date: 2026-06-24

## Automated QA

Passed:

- Python compile for `bot.py`, `services/db.py`, `services/pulse_ads_service.py`, and `scripts/pulse_ads_foundation_audit.py`.
- `scripts/pulse_ads_foundation_audit.py`.

## Verified Behaviors

- Additive schema initializes.
- Placement seeds initialize.
- Unapproved ads are blocked.
- Approved active ads serve.
- Paused campaigns are blocked.
- Kill switch blocks serving.
- Unsafe URLs are rejected.
- Tracking writes persist.
- CSRF is enforced on tracking writes.
- Anonymous admin access is blocked.
- Client payloads are sanitized and do not include private advertiser or targeting data.

## Manual QA Status

No visible ad slots were activated in production surfaces in this change, so there is no new visual overlay, mobile touch, or z-index risk to browser-test on Home yet. Browser QA is required when a specific placement is injected into Home, Dashboard, Statuses, Videos, Search, Marketplace, or Pulse Radio.
