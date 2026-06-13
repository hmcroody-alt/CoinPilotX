# PulseSoc Live Egress Quota and Echo Repair

Date: 2026-06-12

## Root Cause

The previously verified Browser Live bridge still had a brittle failure mode:
when LiveKit rejected Mux egress with `resource_exhausted` / `egress minutes exceeded`, the backend marked Browser Live as failed and returned HTTP 502 even though the host camera and microphone had already published to LiveKit.

That caused three visible regressions:

- The studio fell back to `0 kbps / 0 FPS` and confusing failed state copy.
- Public viewers could be left with stale/generated HLS plus direct WebRTC fallback risk, which can create delayed repeated audio.
- Egress failure text could render over the host video surface.

## Changes Made

- Added explicit LiveKit egress quota detection in `bot.py`.
- Changed `/api/pulse/live/<id>/browser-publish` so quota exhaustion keeps the live session active in `livekit_direct` mode instead of failing the stream.
- Added `direct_mode` to the live state API.
- Updated live playback manifest logic so stale/generated HLS is suppressed when Mux egress is unavailable and direct LiveKit playback is the correct transport.
- Updated stream health labels so PulseSoc does not show fake `Mux active` labels when Mux is not receiving ingest.
- Updated the public live viewer so it renders a real video element for LiveKit direct playback even when no Mux HLS URL exists.
- Removed video-surface error overlays while the host camera is active. Egress status now belongs in the control panel, not over the face/video.
- Made `Tap to unmute` temporary. It hides after a short delay and can reappear when the muted video is tapped.
- Bumped the live runtime cache key to avoid stale mobile/browser JS.

## Files Changed

- `bot.py`
- `services/live_distribution_service.py`
- `services/live_stream_health_service.py`
- `static/js/pulse_live_studio_runtime.js`
- `static/js/pulse_live_studio.js`
- `static/css/pulse_live_studio.css`
- `scripts/live_egress_quota_fallback_audit.py`
- `scripts/pulse_livekit_mux_bridge_audit.py`

## Validation

Passed:

- `python3 -m py_compile bot.py services/live_distribution_service.py services/live_stream_health_service.py scripts/live_egress_quota_fallback_audit.py scripts/pulse_livekit_mux_bridge_audit.py`
- `node --check static/js/pulse_live_studio_runtime.js`
- `node --check static/js/pulse_live_studio.js`
- `python3 scripts/pulse_livekit_mux_bridge_audit.py`
- `venv/bin/python scripts/live_egress_quota_fallback_audit.py`
- `venv/bin/python scripts/live_audio_video_pipeline_audit.py`
- `venv/bin/python scripts/live_media_transport_audit.py`
- `venv/bin/python scripts/live_viewer_playback_audit.py`
- `venv/bin/python scripts/live_studio_audit.py`
- `venv/bin/python scripts/mux_live_audit.py`

## Current Operational Note

The screenshot error `egress minutes exceeded | resource_exhausted` indicates the active LiveKit account/project has exhausted egress minutes. Until LiveKit egress capacity is restored, PulseSoc should keep Browser Live running through LiveKit direct playback and avoid pretending Mux is active.

When LiveKit egress minutes are available again, the same Browser Live path can resume forwarding to Mux for HLS/replay.
