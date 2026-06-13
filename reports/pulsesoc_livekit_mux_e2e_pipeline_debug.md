# PulseSoc LiveKit to Mux Livestream Pipeline Debug

Date: 2026-06-13

## Scope

Primary pipeline:

User camera/mic -> LiveKit room -> LiveKit egress -> Mux RTMP ingest -> Mux playback URL -> PulseSoc public viewer page.

This pass intentionally avoided a Live Studio redesign. The changes target connection timing, duplicate publishing, backend egress gating, safe diagnostics, and audit coverage.

## Root Cause Found

The livestream pipeline could start Mux egress before LiveKit had a fully connected host participant with a published video track. The backend trusted the browser-reported track counts and could mark a session live before verified LiveKit room state existed. That timing mismatch matches the observed failure mode:

- LiveKit egress rejected.
- Mux remained idle.
- 0 kbps / 0 FPS.
- Error: publishing rejected as engine not connected within timeout.

Additional contributing issues:

- Browser Live did not explicitly guard all publish paths behind a confirmed LiveKit connected state.
- Duplicate join/publish attempts could be triggered by repeated UI actions.
- Session and stream rows were initially created with `live` status too early, before egress/playback was confirmed.

## Pipeline Fixed

### Frontend LiveKit Publishing

- Browser publishing now waits for the LiveKit room to reach connected state before publishing camera/mic tracks.
- Duplicate LiveKit room joins are deduped with a shared connection promise.
- Duplicate local track publishes are deduped with publish state and published track IDs.
- Local publish telemetry records connected state and published audio/video counts without logging private tokens.
- Browser publish retries backend egress start when the backend reports `waiting_for_tracks`.

### Backend Livestream Session

- New livestream sessions and stream rows now start as `starting`, not `live`.
- Host browser publishing validates reported audio/video track counts.
- The backend inspects the LiveKit room through `RoomService/ListParticipants` before egress.
- The backend requires the host participant to be joined and have at least one unmuted video track before starting egress.
- If LiveKit is not ready, the API returns HTTP 409 with `retryable: true`, stores `livekit_waiting_for_tracks`, and does not call egress.

### LiveKit Egress to Mux

- Egress starts only after verified LiveKit room track readiness.
- The room name comes from the livestream's `webrtc_room_id`.
- The Mux RTMP destination remains `rtmp://global-live.mux.com:5222/app/<stream_key>`.
- Stream keys are never returned in public viewer data and diagnostics mask RTMP destinations.
- Egress state and errors are persisted safely.

### Mux Playback

- Existing Mux service and audit coverage confirm the Mux RTMP base URL, playback URL generation, webhook signature validation, and live status update paths.
- Viewer state prefers Mux HLS playback when available and does not expose RTMP ingest or stream keys.

## Files Changed

- `bot.py`
  - Session/stream creation now uses `starting`.
  - Added LiveKit room admin token generation.
  - Added LiveKit participant/track inspection.
  - Added host video readiness wait before egress.
  - Added retryable `waiting_for_tracks` browser-publish response.
- `static/js/pulse_live_studio_runtime.js`
  - Added connected-state wait.
  - Added duplicate join/publish protection.
  - Added publish telemetry and retry-on-409 behavior.
- `static/js/pulse_live_studio.js`
  - Synced runtime bundle changes.
- `scripts/livekit_mux_egress_gate_audit.py`
  - New audit proving egress is not started before host video track readiness.
- `scripts/pulse_livekit_mux_bridge_audit.py`
  - Added static checks for LiveKit room inspection, waiting state, and frontend retry/deduping.
- `scripts/live_audio_video_pipeline_audit.py`
  - Updated to account for verified track readiness.
- `scripts/live_media_transport_audit.py`
  - Updated to account for verified track readiness.
- `scripts/live_egress_quota_fallback_audit.py`
  - Updated to account for verified track readiness.

## Validation Results

### Syntax

PASS:

```text
.venv/bin/python -m py_compile bot.py services/mux_live_service.py services/live_distribution_service.py scripts/livekit_mux_egress_gate_audit.py scripts/live_audio_video_pipeline_audit.py scripts/live_media_transport_audit.py scripts/live_egress_quota_fallback_audit.py scripts/pulse_livekit_mux_bridge_audit.py scripts/pulse_livekit_webhook_audit.py
node --check static/js/pulse_live_studio_runtime.js
node --check static/js/pulse_live_studio.js
```

### Pipeline Audits

PASS:

```text
.venv/bin/python scripts/livekit_mux_egress_gate_audit.py
.venv/bin/python scripts/pulse_livekit_mux_bridge_audit.py
.venv/bin/python scripts/live_audio_video_pipeline_audit.py
.venv/bin/python scripts/live_egress_quota_fallback_audit.py
.venv/bin/python scripts/live_media_transport_audit.py
.venv/bin/python scripts/live_viewer_playback_audit.py
.venv/bin/python scripts/live_studio_audit.py
.venv/bin/python scripts/pulse_livekit_webhook_audit.py
.venv/bin/python scripts/mux_live_audit.py
git diff --check
```

Key proof from audits:

- Browser publish returns retryable 409 while host video is missing.
- LiveKit egress is not started before video track readiness.
- Session remains `starting` while egress waits.
- Browser publish starts LiveKit-to-Mux egress after verified tracks.
- Session marks live only after egress start in the success path.
- Viewer state exposes playable HLS when Mux playback is available.
- Mux service uses `rtmp://global-live.mux.com:5222/app`.
- Mux webhook signature validation accepts valid signatures and rejects invalid signatures.
- Viewer playback avoids fake placeholder playback and avoids exposing RTMP ingest.

### Local Configuration Check

BLOCKED for real external E2E:

```text
app_livekit_configured=False
app_mux_configured=False
app_mux_webhook_configured=False
```

Because this local workspace does not have LiveKit or Mux credentials configured, I could not honestly prove:

- A real host participant in the external LiveKit dashboard.
- A real LiveKit egress job accepted by LiveKit cloud.
- Mux status changing from idle to active.
- Real global Mux HLS playback from a live stream.

## Browser / Route QA Notes

The in-app browser session previously loaded:

- `/pulse/live/studio/603`
- `/pulse/live/603`

Observed behavior from the authenticated in-app browser context:

- Studio route loaded.
- Non-host studio access did not expose camera-start controls.
- Viewer route loaded with player scaffolding.
- Viewer body did not expose `rtmp://`.

Unauthenticated curl checks correctly redirected to login:

```text
/pulse/live/603 -> /login?next=/pulse/live/603
/pulse/live/studio/603 -> /login?next=/pulse/live/studio/603
```

This confirms route protection in local HTTP checks, but it also means public anonymous viewer access is not proven here. If PulseSoc requires truly public anonymous viewing, the viewer route access policy must be decided and tested separately.

## Security Checks

PASS locally:

- No stream key is logged by the new diagnostics.
- RTMP destination errors are sanitized.
- Viewer routes/audits verify no private RTMP ingest is exposed.
- LiveKit inspection uses short-lived room admin token server-side only.
- Host-only studio route still blocks non-host camera controls.
- Mux webhook signature validation remains enforced.

## Remaining External Gate

The code is locally repaired and audit-validated, but the final user-required gate is not satisfied until a real configured environment proves:

1. Start livestream as host.
2. Confirm host participant in the LiveKit room.
3. Confirm audio/video tracks published in LiveKit.
4. Confirm egress starts after the host video track exists.
5. Confirm Mux live stream changes from idle to active.
6. Confirm Mux playback ID/HLS URL is saved.
7. Confirm another browser/device can watch.
8. End stream and confirm LiveKit/Mux cleanup.

Per the execution requirement, this should not be committed or pushed until that real E2E proof is completed in a LiveKit/Mux-configured environment.
