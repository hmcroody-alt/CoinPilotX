# PulseSoc LiveKit-to-Mux Regression Report

Date: 2026-06-13

## Exact Failure Found

The regression was not caused by missing LiveKit quota. LiveKit Cloud shows the project is on the Ship plan with 600 included transcode minutes and 84 minutes used for June 2026.

The latest observed LiveKit egress did start:

- Egress ID: `EG_7VwuwPHXxEGP`
- Status progression: `EGRESS_STARTING` -> `EGRESS_ACTIVE` -> `EGRESS_ENDING` -> `EGRESS_COMPLETE`
- End reason: `Source closed`
- Duration: 4 seconds
- Source room: `pulse-webrtc-7b02a45b216346c4`

Mux received RTMP briefly:

- Mux live stream ID: `f01m01q6Kc695yNxwNU2bvu02ZTrwyuTJ9Bo02b87vcbYA8`
- Mux events: `connected` at 1:25:41 AM, then `disconnected` at 1:25:42 AM
- Current Mux state: `idle`
- Incoming stream health: ingest not connected

The active PulseSoc production session inspected afterward was stale:

- PulseSoc live ID: `25`
- Status shown in app: `live` / `publishing`
- `mux_live_stream_id`: empty
- `mux_playback_id`: empty
- `livekit_egress_id`: empty
- `webrtc_room_id`: old room `pulse-webrtc-a75a10e094984aa4`
- Health: 0 kbps / 0 FPS

Root cause: the app could keep or expose stale live sessions and stale fallback playback while Mux was idle. LiveKit egress ending with `Source closed` did not force the session out of public-live state, and new sessions did not hard-clean old LiveKit rooms, old egresses, or old Mux live streams before starting.

## Fix Implemented

- Added stale live cleanup before creating a new live session.
- Stops stale LiveKit egress via `StopEgress`.
- Deletes stale LiveKit rooms via `DeleteRoom`.
- Disables stale Mux live streams before replacement.
- Creates new sessions as `starting` / `ready`, not public `live`.
- Suppresses follower live notifications until Mux confirms active.
- Restricts live discovery to `status='live'` and `mux_live_status IN ('active','live')`.
- Starts LiveKit egress only after host video track readiness remains confirmed.
- Does not mark stream public-live when egress merely starts.
- Treats LiveKit `egress_ended` / source-closed as non-live with a safe failure reason.
- Syncs Mux state from the Mux API.
- Hides stale HLS URLs until Mux status is actually `active` or `live`.
- Treats LiveKit direct as unpublished fallback, not working public Mux ingest.
- Fixes the runtime `muxStatus` scoping issue in live state UI updates.

## Files Changed In This Pass

- `bot.py`
- `services/live_distribution_service.py`
- `services/live_stream_health_service.py`
- `static/js/pulse_live_studio_runtime.js`
- `static/js/pulse_live_studio.js`
- `scripts/live_mux_regression_audit.py`
- Updated livestream regression/audit expectations:
  - `scripts/live_audio_video_pipeline_audit.py`
  - `scripts/live_media_transport_audit.py`
  - `scripts/live_viewer_playback_audit.py`
  - `scripts/live_egress_quota_fallback_audit.py`

## Validation Passed

- `scripts/live_mux_regression_audit.py`
- `scripts/live_audio_video_pipeline_audit.py`
- `scripts/live_media_transport_audit.py`
- `scripts/live_viewer_playback_audit.py`
- `scripts/live_egress_quota_fallback_audit.py`
- `scripts/pulse_livekit_mux_bridge_audit.py`
- `scripts/livekit_mux_egress_gate_audit.py`
- `scripts/live_audio_echo_prevention_audit.py`
- `scripts/pulse_all_video_sound_policy_audit.py`
- `node --check static/js/pulse_live_studio_runtime.js`
- `node --check static/js/pulse_live_studio.js`
- Python compile for touched backend/services/audits
- `git diff --check`

## Real QA Gate

Not passed yet. These changes are local and have not been committed, pushed, deployed, or tested in production.

Required remaining production proof:

1. Start a fresh livestream after deployment.
2. Confirm stale live ID `25` no longer appears as public live unless Mux is active.
3. Confirm LiveKit host joins a fresh room.
4. Confirm host video and audio tracks publish.
5. Confirm egress starts and stays active.
6. Confirm Mux status changes `idle` -> `active`.
7. Confirm Mux shows kbps/FPS.
8. Confirm a second browser/device receives public HLS playback with audio/video.

## Commit Status

No commit or push was performed because the requested final gate requires real public playback to work first.

