# LiveKit + Mux Configuration Verification

Date: 2026-06-13

## Summary

This verification inspected the actual Railway, LiveKit Cloud, Mux Dashboard, and production PulseSoc app environments. It does not rely on the local `.env`.

Result:

- Railway production environment is configured with the required LiveKit and Mux variable names.
- LiveKit Cloud project is active and receives PulseSoc room sessions.
- LiveKit host join and track publishing are proven from dashboard session events.
- LiveKit egress is enabled historically, but the latest host session had no egress record.
- Mux live streams exist and are configured for RTMP ingest and public playback.
- Current Mux live streams are idle unless LiveKit egress connects to Mux.
- The QA Browser real-start attempt failed before LiveKit publish because Chrome camera/microphone permission is blocked in the browser context.

## Railway Verification

Project:

- `coinpilotx-alert-worker`
- Environment: `production`
- Primary web service: `CoinPilotX`
- Domain: `pulsesoc.com`
- Service status: `Online`

Required variables found on the `CoinPilotX` production service:

- `LIVEKIT_URL`: present
- `LIVEKIT_API_KEY`: present
- `LIVEKIT_API_SECRET`: present
- `MUX_TOKEN_ID`: present
- `MUX_TOKEN_SECRET`: present
- `MUX_WEBHOOK_SECRET`: present

Values were masked in Railway and were not copied.

Evidence:

- `reports/pulsesoc-evidence/railway-livekit-mux-vars-2026-06-13.png`

CLI note:

- `railway whoami` and `railway status` are not usable locally because the CLI OAuth token is expired.
- Browser dashboard auth is valid and was used instead.

## LiveKit Cloud Verification

Project:

- `PulseSoc`
- Project URL path: `/projects/p_2kh7i9ypewf`

Dashboard findings:

- Project is active and showing metrics.
- LiveKit displays a banner: `Your project has exceeded its free tier limit. Please upgrade now.`
- Billing page shows current plan `build`.
- June 2026 usage includes:
  - WebRTC participant minutes: 104 min
  - Downstream data transfer: 1 GB
  - Included transcode minutes: 60 min
  - Additional egress transcode minutes - video: 24 min
- Egresses page shows historical completed Room Composite egresses on June 10 and June 11.

Evidence:

- `reports/pulsesoc-evidence/livekit-overview-free-tier-exceeded-2026-06-13.png`
- `reports/pulsesoc-evidence/livekit-billing-quota-2026-06-13.png`
- `reports/pulsesoc-evidence/livekit-egresses-2026-06-13.png`

## LiveKit Room / Participant / Track Verification

Latest inspected LiveKit session:

- Session: `RM_AvTHSCEtZkYs`
- Room: `pulse-webrtc-abea442ee64c4d3f`
- Start: June 13, 2026 at 12:19:03 AM
- End: June 13, 2026 at 12:22:05 AM
- Unique participants: 1
- Host identity: `pulse-user-1`
- Host name: `ROODY CHERIE`

Dashboard proof:

- Host joined as publisher.
- Participant detail shows `Track published` events for two tracks at June 13, 2026 12:20:44 AM.
- Track unpublished events occurred when the participant left.
- Session detail shows no egress rows for that session.

Interpretation:

- User camera/mic -> LiveKit room works for at least one real production session.
- The failure for that inspected session is after LiveKit track publish and before/at LiveKit egress start.

Evidence:

- `reports/pulsesoc-evidence/livekit-latest-session-detail-2026-06-13.png`
- `reports/pulsesoc-evidence/livekit-latest-participant-detail-2026-06-13.png`

## Mux Dashboard Verification

Mux environment:

- Organization path: `/organizations/50i47f`
- Environment path: `/environments/1nkm64`

Live stream findings:

- Mux live streams exist.
- Visible streams are currently `Idle`.
- Recent stream `Qu201CNsPlu536f5Hm1Z1YGlLQLE01yJWA2Qi1xMeGL600` is enabled and idle.
- RTMP ingest URL shown by Mux:
  - `rtmp://global-live.mux.com:5222/app`
  - `rtmps://global-live.mux.com:443/app`
- Playback ID exists and is public.
- Mux event sequence for that historical stream reached:
  - `created`
  - `connected`
  - `recording`
  - `active`
  - `disconnected`
  - `idle`
- Current state for that stream:
  - `idle`
  - `Ingest is not connected`
  - `No active asset`

Webhook findings:

- Webhook endpoint exists and is enabled:
  - `https://coinpilotx.app/api/pulse/live/mux/webhook`
- The dashboard exposed the webhook signing secret; the screenshot containing it was deleted and is not included.

Evidence:

- `reports/pulsesoc-evidence/mux-live-streams-idle-list-2026-06-13.png`
- `reports/pulsesoc-evidence/mux-recent-live-stream-detail-2026-06-13.png`
- `reports/pulsesoc-evidence/mux-playback-policy-2026-06-13.png`

## Real Production Start Attempt

Production app route:

- `https://pulsesoc.com/pulse/live`

Action:

- Clicked `Start Live`.

Result:

- Production created Live Studio `72`.
- Studio URL:
  - `https://pulsesoc.com/pulse/live/studio/72`
- Assigned LiveKit room:
  - `pulse-webrtc-432ed6bd8fb64fd1`
- Assigned Mux HLS playback URL was present in Studio.
- Mux status remained `idle`.
- Bitrate remained `0 kbps`.
- FPS remained `0 FPS`.
- Camera remained off.

Blocking failure in this QA Browser:

- Clicking `Start Camera` failed with:
  - `Camera/microphone permission is blocked in Chrome. Click the site controls icon in the address bar, allow camera and microphone, then reload.`
- Because the QA Browser blocked camera/microphone, this test did not create a LiveKit session for `pulse-webrtc-432ed6bd8fb64fd1`.
- LiveKit Sessions page after the attempt did not show room `pulse-webrtc-432ed6bd8fb64fd1`.

Evidence:

- `reports/pulsesoc-evidence/pulsesoc-live-setup-before-start-2026-06-13.png`
- `reports/pulsesoc-evidence/pulsesoc-live-camera-permission-needed-2026-06-13.png`
- `reports/pulsesoc-evidence/pulsesoc-live-studio-72-before-camera-2026-06-13.png`
- `reports/pulsesoc-evidence/pulsesoc-live-studio-72-after-camera-attempt-2026-06-13.png`
- `reports/pulsesoc-evidence/livekit-sessions-after-studio-72-attempt-2026-06-13.png`

Cleanup note:

- I attempted to end test live `72`, but browser automation timed out on the destructive `End Stream` click.
- The test stream should be manually ended from `https://pulsesoc.com/pulse/live/studio/72` if it still appears as starting/warming.

## Exact Failure Stage

There are two verified failure modes:

1. QA Browser test on June 13, 2026:
   - Failure stage: browser camera/microphone permission.
   - The stream never reached LiveKit because camera/mic access was blocked.
   - Evidence: Studio message and absence of `pulse-webrtc-432ed6bd8fb64fd1` in LiveKit sessions.

2. Latest real LiveKit host session on June 13, 2026 at 12:19 AM:
   - Host joined LiveKit.
   - Two tracks published.
   - No egress record exists for that session.
   - Mux did not receive ingest for that session.
   - Failure stage: after LiveKit track publish, at or before backend LiveKit egress start.

Configuration is not missing in Railway production. The next implementation step should target egress start reliability and diagnostics for the already-published-track case, plus explicit UX recovery for browser camera/mic permission blocking.

## Do Not Continue Blindly

Implementation should continue from the verified failure point:

- Add/verify production logging for `/api/pulse/live/<id>/browser-publish`.
- Confirm whether production code starts egress immediately after track publish or fails before calling LiveKit egress.
- Persist and surface LiveKit egress API response/error for the real session.
- Keep the backend egress readiness gate already implemented locally.
- Add a clear permission recovery flow when browser camera/microphone is blocked.
