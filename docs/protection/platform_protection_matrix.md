# PulseSoc Platform Protection Matrix

Protected systems:

- Livestream
- Reels
- Videos
- Statuses/stories
- Chat/messages
- Notifications/alerts
- Uploads
- Camera
- Audio
- Reactions
- Comments
- Follows
- Profiles
- Search
- Feed
- Payments/premium
- Creator tools
- Admin tools
- Auth/security
- Mobile navigation
- Desktop navigation

## Guardrails

- Golden path checks live in `/tests/protection`.
- The protection runner is `/scripts/protection/run_protection_suite.py`.
- CI runs the protection suite through `.github/workflows/protection.yml`.
- Critical media and livestream changes must pass protection tests before and after modification.
- Static protection checks do not replace real browser/device QA for livestream, push, checkout, uploads, or mobile release work.

## Current Coverage

- Livestream: LiveKit/Mux bridge, host mute, egress fallback, webhook presence, secret handling indicators.
- Media playback: Reels autoplay with sound preference, next-two preload, status preview mute safety, active status viewer sound preference, mobile Videos drawer.
- Camera: 1080p/720p profile, safe diagnostics, Banuba runtime status.
- Core routes: critical Pulse pages, APIs, Stripe webhook route, unread count contracts, migration-safe domains.

## Remaining Gaps

- Real LiveKit/Mux integration tests require paid egress minutes and deployed QA credentials.
- Real push notification hard-alert tests require physical devices.
- Payment fulfillment tests require Stripe test/live dashboard events.
- Full rollback automation depends on the deployment provider and should be wired after the protection suite is stable.
