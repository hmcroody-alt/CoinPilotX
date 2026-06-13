# PulseSoc LiveKit/Mux Source Closed Investigation

Date: 2026-06-13

## Current Finding

The pipeline is no longer failing at initial connection. The latest production evidence showed:

- LiveKit egress started.
- LiveKit egress became active.
- Mux connected and left idle.
- Mux disconnected roughly one second later.
- LiveKit egress completed after roughly four seconds with end reason: `Source closed`.

That means RTMP ingest, Mux stream key, LiveKit egress startup, and Mux acceptance were working for that attempt. The failure is the upstream source lifecycle after startup.

## Evidence Timeline From Dashboard Review

- Room: `pulse-webrtc-7b02a45b216346c4`
- LiveKit egress: `EG_7VwuwPHXxEGP`
- Egress start: 2026-06-13 01:25:38 UTC
- Egress active: 2026-06-13 01:25:40 UTC
- Egress complete: 2026-06-13 01:25:43 UTC
- End reason: `Source closed`
- Mux live stream connected at 2026-06-13 01:25:41 UTC
- Mux live stream disconnected at 2026-06-13 01:25:42 UTC

LiveKit room/session detail showed the host identity reconnecting repeatedly around the failure window, while later audio/video track publish events appeared after the first egress had already ended. This strongly indicates egress was tied to an unstable/transient host participant or track state.

## Root Cause

Most likely root cause:

1. Backend egress startup used participant egress first.
2. The browser host identity/session was unstable during startup, creating repeated participant sessions.
3. Participant egress attached to a transient participant source.
4. That participant source closed shortly after Mux connected, causing `Source closed`.

Secondary contributing gap:

- Existing diagnostics were mostly console-only, so `track ended`, `room disconnected`, `participant disconnected`, cleanup reason, and exact provider end reason were not persisted in one backend timeline.

## Fix Implemented

### Browser Publisher

- Added safe browser-to-backend live debug events.
- Logs these source lifecycle events:
  - room connecting
  - room connected
  - room disconnected
  - room reconnecting
  - room reconnected
  - participant joined
  - participant disconnected
  - audio/video track published
  - audio/video track unpublished
  - track ended
  - track muted/unmuted
  - cleanup started/completed
  - publish request started/acknowledged
  - egress start response
- Cleanup now records a reason, including `pagehide`, `start_camera_restart`, and `start_screen_restart`.

### Backend Session Manager

- Added `pulse_live_record_timeline_event`.
- Added `pulse_live_safe_debug_payload` to redact:
  - tokens
  - secrets
  - stream keys
  - RTMP/private URLs
  - authorization values
- Added host/admin-only endpoint:
  - `POST /api/pulse/live/<live_id>/debug-event`
- Stale session cleanup now records:
  - `live_session_cleanup_started`
  - `live_session_cleanup_completed`
- Public playback manifests now blank ingest-side RTMP URLs so viewer state does not expose private broadcast destination data.

### LiveKit Track Stability

- `pulse_livekit_wait_for_host_tracks` now requires stable consecutive host video-track checks before egress starts.
- Default stability requirement: `LIVEKIT_TRACK_STABLE_CHECKS=3`.
- This avoids starting egress from a one-snapshot transient participant/track.

### LiveKit Egress Strategy

- Browser live egress now prefers `StartRoomCompositeEgress`.
- `StartParticipantEgress` remains available as fallback.
- Reason: room composite is less fragile when the host participant instance reconnects or is replaced during startup.

### Webhook Handling

- LiveKit webhook now persists lifecycle events into `pulse_live_events`.
- `egress_ended` now preserves provider end reason, including `Source closed`.
- `Source closed` is stored as `egress_source_closed` instead of being hidden behind a generic ended message.

## Files Changed

- `bot.py`
- `services/live_distribution_service.py`
- `static/js/pulse_live_studio_runtime.js`
- `static/js/pulse_live_studio.js`
- `scripts/live_source_closed_diagnostics_audit.py`
- `scripts/pulse_livekit_mux_bridge_audit.py`
- `scripts/live_media_transport_audit.py`

## Validation Run

Passed:

- `python -m py_compile` for changed backend/services/audit files.
- `node --check static/js/pulse_live_studio_runtime.js`
- `node --check static/js/pulse_live_studio.js`
- `scripts/live_source_closed_diagnostics_audit.py`
- `scripts/live_mux_regression_audit.py`
- `scripts/live_audio_video_pipeline_audit.py`
- `scripts/live_media_transport_audit.py`
- `scripts/live_viewer_playback_audit.py`
- `scripts/live_egress_quota_fallback_audit.py`
- `scripts/pulse_livekit_mux_bridge_audit.py`
- `scripts/live_audio_echo_prevention_audit.py`
- `git diff --check`

## Required Real QA Gate

Not yet passed in this local code state.

The user-required final gate remains:

- Deploy this fix.
- Start a real livestream.
- Observe 0 sec, 2 sec, 5 sec, 10 sec, 30 sec, 60 sec, and 5 minutes.
- Confirm host remains connected.
- Confirm audio/video tracks remain published.
- Confirm LiveKit egress remains active.
- Confirm Mux stays active and does not return idle.
- Confirm a second browser/device receives live audio/video.
- Confirm no `Source closed`, room disconnect, track ended, or Mux idle transition occurs.

No commit or push should happen until that 5-minute real stream QA passes.
