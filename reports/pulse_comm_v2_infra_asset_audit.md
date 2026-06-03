# Pulse Communications V2 Infrastructure Asset Audit

Date: 2026-06-03

## Scope

Read-only QA browser inspection of currently available production infrastructure assets that can support Pulse Communications V2. No secrets were exposed, copied, or edited.

## Railway Assets Observed

Production project is accessible and authenticated in the QA browser.

Services visible and online:

- `CoinPilotX` web service on `coinpilotx.app`
- `Postgres`
- `coinpilotx-media-engine`
- `coinpilotx-undx-worker`
- `coinpilotx-pulse-worker`
- `python alert_worker.py`
- `python telegram_worker.py`

CoinPilotX web service variables showed 61 service variables. Safe name-only inspection confirmed:

- `DATABASE_URL`: present
- `MUX_TOKEN_ID`: present
- `MUX_TOKEN_SECRET`: present
- `MUX_WEBHOOK_SECRET`: present
- `MUX_DATA_ENV_KEY`: present
- `MUX_SOURCE_BASE_URL`: present
- `R2_ACCESS_KEY_ID`: present
- `R2_SECRET_ACCESS_KEY`: present
- `R2_ACCOUNT_ID`: present
- `R2_BUCKET`: present
- `R2_ENDPOINT_URL`: present
- `R2_PUBLIC_BASE_URL`: present
- `TWILIO_ACCOUNT_SID`: present
- `TWILIO_AUTH_TOKEN`: present

Not confirmed in the visible variable pass:

- `TWILIO_FROM_NUMBER`
- `PULSE_COMMUNICATIONS_V2_ENABLED`
- `COMM_V2_TWILIO_NOTIFICATIONS_ENABLED`
- `COMM_V2_TWILIO_DRY_RUN`

## Cloudflare

Cloudflare dashboard is currently protected by human verification in the QA browser. I did not bypass or solve the challenge. Cloudflare inspection remains pending until the owner completes the verification prompt.

Known useful Cloudflare assets from Railway variables:

- R2 bucket credentials exist.
- R2 endpoint URL exists.
- R2 public base URL exists.
- Mux source base URL exists, which should let Mux fetch source media without using challenge-protected `cdn.coinpilotx.app` URLs.

## Mux

Mux dashboard is currently at the login screen in the QA browser. I did not log in or attempt account access.

Known useful Mux assets from Railway variables:

- Mux token ID/secret exist.
- Mux webhook secret exists.
- Mux data env key exists.
- Mux source base URL exists.

These are enough for Communications V2 to support video-message Mux asset creation and future live-room playback foundations from code, pending production testing.

## Current Communications V2 Readiness

Existing local foundation already includes:

- `/pulse/messages-v2`
- `/api/pulse/communications/v2/*`
- V2 conversation/message/participant tables
- V2 attachment table with R2/Mux fields
- V2 attachment upload endpoint
- Mux live stream routes and webhook signature verification
- Twilio notification service in safe dry-run mode by default
- Admin-only infrastructure diagnostics endpoint

## Safe Improvements Applied

- Communications V2 page now loads the shared Pulse media renderer.
- V2 attachments now use the unified Pulse media renderer for image/video/audio attachments when available.
- Video message attachments can now use Mux HLS playback URLs through the same player foundation as Feed/Reels/Status.
- V2 chat media is constrained inside message bubbles so renderer styling does not dominate the conversation thread.
- V2 infrastructure diagnostics now include optional Mux/R2 source variables and readiness booleans for:
  - attachments
  - video messages
  - live rooms
  - SMS notifications
- `/admin/pulse-infrastructure` now provides an owner/admin-safe readiness view for Database, R2, Mux video, Mux live rooms, Twilio alerts, and the Communications V2 feature flag.
- `/pulse/messages-v2` now uses the darker Pulse visual language while preserving the two-column messenger layout and avoiding placeholder side panels.

## Recommendations

1. Keep Communications V2 feature-flagged until production QA passes.
2. Add `TWILIO_FROM_NUMBER` only when SMS sending should move beyond dry-run.
3. Keep `COMM_V2_TWILIO_DRY_RUN=true` until notification templates and consent flows are approved.
4. Use R2 for all message attachments.
5. Use Mux for video message playback whenever Mux asset creation succeeds.
6. Use Mux live routes for future room broadcasts, exposing stream keys only to the host.
7. Complete Cloudflare dashboard verification before adding any narrow WAF/upload rules for Communications V2 attachments.
8. Complete Mux dashboard login before verifying asset counts and webhook delivery.
9. Use `/admin/pulse-infrastructure` as the production checklist before exposing Communications V2 more broadly.
