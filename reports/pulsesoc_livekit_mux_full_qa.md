# PulseSoc LiveKit to Mux Full QA

Date: 2026-06-11

## Scope

Verified the Browser Live path:

Browser camera/microphone -> LiveKit room -> LiveKit room-composite egress -> Mux RTMP ingest -> Mux HLS playback -> PulseSoc public viewer.

No secrets, RTMP stream keys, API keys, or webhook signing keys are included in this report.

## LiveKit Verification

- Project: PulseSoc.
- Room verified: `pulse-webrtc-f94d708181df43d1`.
- Host participant joined and published camera/microphone tracks from the browser.
- LiveKit dashboard showed the room active after Start Camera.
- Egress verified:
  - Egress ID: `EG_HLP44fWw3Tx7`
  - Type: Room composite
  - Destination: Mux RTMP destination, stream key masked
  - Status: Active
- No LiveKit egress failure was visible during the successful run.

## Mux Verification

- Mux live stream verified active:
  - Live stream ID: `IRgGEPhXonijD1uAaGsNQpIPttTWBoe00ufRUHJgP3x00`
  - Active asset ID: `ZB8DMgTI00sbyg602v00th01yqehpT42BuUmAqJ7urzn9bk`
- Mux events observed:
  - `video.live_stream.connected`
  - `video.live_stream.recording`
  - `video.live_stream.active`
- Mux input health observed in dashboard:
  - Video bitrate average: `3,001 Kbps`
  - Audio bitrate average: `132 Kbps`
  - Frame rate average: `30.00 fps`
  - Resolution: `1280 x 720`
  - Video codec: `avc1`
  - Audio codec: `mp4a`
- Result: Mux no longer stayed idle at `0 kbps / 0 FPS` during Browser Live.

## PulseSoc Studio Verification

- Studio URL tested: `/pulse/live/studio/46`.
- Start Camera succeeded after Chrome camera/microphone permission was allowed.
- The previous large `R` / "Camera preview ready" overlay did not cover the host camera after the stream was active.
- Browser console confirmed:
  - Local browser stream acquired.
  - LiveKit publish acknowledged.
- Studio route is updated to load the patched runtime:
  - `/static/js/pulse_live_studio_runtime.js?v=20260611-mux-health-v1`
- Studio now displays active Mux ingest as `Mux active` instead of stale zero metrics when detailed Mux health statistics are not available through the app runtime.

## Public Playback Verification

- Public viewer URL tested: `/pulse/live/46`.
- Public viewer used Mux HLS playback.
- HLS playback URL host: `stream.mux.com`.
- Browser video element was playing with:
  - Ready state: `4`
  - Video dimensions: `1280 x 720`
  - Progressing current time
- Public viewers receive playback only. RTMP ingest URL and stream key remain host/admin-only.

## Railway / Environment Verification

The connected production service had the required variable names present in Railway during QA:

- `LIVEKIT_URL`
- `LIVEKIT_API_KEY`
- `LIVEKIT_API_SECRET`
- `MUX_TOKEN_ID`
- `MUX_TOKEN_SECRET`
- `MUX_WEBHOOK_SECRET`
- `MUX_DATA_ENV_KEY`
- `MUX_SOURCE_BASE_URL`

Values were not copied into this report. Stream keys remain masked in the Studio UI.

## Webhook Status

- Mux webhook verification exists in code through `verify_mux_webhook_signature`.
- Mux live stream events are handled for connected/disconnected and asset ready/error paths.
- LiveKit webhook handling and egress state synchronization are present in the live bridge code path.

## Code Changes From Final QA Pass

- Active Mux ingest now contributes to live health state without fabricating numeric bitrate/FPS values.
- Studio Mux polling returns `ingest_active`, `bitrate_label`, and `fps_label`.
- Studio UI shows `Mux active` when Mux is active but app-level detailed health metrics are unavailable.
- Public viewer skips WebRTC fallback signaling when a Mux HLS source is already available, avoiding misleading failed viewer-offer logs.
- Studio runtime query string was bumped to force browsers to fetch the patched file.

## Validation

Passed:

- `node --check static/js/pulse_live_studio_runtime.js`
- `node --check static/js/pulse_live_studio.js`
- `python3 -m py_compile bot.py services/live_stream_health_service.py services/mux_live_service.py scripts/live_studio_audit.py scripts/pulse_livekit_webhook_audit.py scripts/pulse_livekit_mux_bridge_audit.py scripts/pulse_mux_live_studio_truth_audit.py`
- `python3 scripts/pulse_livekit_mux_bridge_audit.py`
- `python3 scripts/pulse_mux_live_studio_truth_audit.py`
- `venv/bin/python scripts/live_studio_audit.py`
- `venv/bin/python scripts/mux_live_audit.py`

Pending final command:

- `git diff --check`

## Result

Browser Live is verified end to end:

- Host camera appears with no face-covering overlay.
- LiveKit room has active browser publisher.
- LiveKit egress is active.
- Mux receives the stream.
- Mux bitrate and FPS are greater than zero in the dashboard.
- Public PulseSoc viewer plays the stream through Mux HLS.
