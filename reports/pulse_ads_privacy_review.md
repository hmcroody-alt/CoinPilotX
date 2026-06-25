# PulseSoc Ads Privacy Review

Date: 2026-06-24

## Privacy Position

The ads foundation is contextual-first and avoids sensitive targeting. Client payloads only include the fields needed to render and interact with a sponsored unit.

## Protected Data

The client ad response does not include:

- owner user id
- advertiser email
- advertiser phone
- targeting JSON
- min or max age
- internal moderation state
- audit log details
- private IDs beyond campaign/creative identifiers needed for tracking

## Tracking

- Impression, viewability, click, and event tracking are stored server-side.
- Viewer user id can be stored for logged-in frequency caps, but public payloads do not expose other users.
- Ad events store only bounded metadata and hashed viewer identifiers where metadata needs a user reference.

## Targeting Rules

- No exact location targeting.
- No sensitive categories.
- No private messages, private statuses, or blocked-user data are used.
- `privacy_preferences.personalized_ads_opt_out` remains available for future expansion; this foundation does not add invasive personalization.
