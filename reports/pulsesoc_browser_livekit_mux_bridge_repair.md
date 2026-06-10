# PulseSoc Browser LiveKit to Mux Bridge Repair

Date: 2026-06-10

## Root Cause

PulseSoc Live Studio was showing a real browser camera preview, but the media stayed local to the browser/custom WebRTC fallback. The backend `/api/pulse/live/<id>/browser-publish` endpoint stored `browser_preview` and explicitly required an external RTMP encoder, so Mux correctly stayed idle at `0 kbps / 0 FPS`.

The Studio also displayed a large centered ready overlay with the host initial, which covered the host camera feed after permission succeeded.

## Fixes

- Removed the persistent camera-ready overlay from active camera playback.
- Added an active-camera CSS guard so the idle overlay is hidden as soon as camera publishing starts.
- Moved camera status into a small toast outside the center of the video.
- Loaded the LiveKit browser client in Live Studio.
- Browser Start Camera now:
  - gets a host-scoped LiveKit token,
  - joins the session LiveKit room,
  - publishes microphone and camera tracks to LiveKit,
  - calls the backend to start Mux forwarding.
- Backend now starts LiveKit Room Composite Egress through:
  - `POST /twirp/livekit.Egress/StartRoomCompositeEgress`
  - `roomRecord` scoped server token
  - RTMP stream output pointing at the Mux ingest URL plus stream key.
- Browser publish now records `browser_live_egress` instead of `browser_preview`.
- Stream keys are masked by default in Studio and only available to host/admin Studio copy controls.
- OBS/RTMP copy is now labeled as optional advanced backup.

## Files Changed

- `bot.py`
- `static/js/pulse_live_studio.js`
- `static/css/pulse_live_studio.css`
- `scripts/live_studio_audit.py`
- `scripts/live_audio_video_pipeline_audit.py`
- `scripts/pulse_livekit_mux_bridge_audit.py`
- `scripts/pulse_mux_live_publish_path_audit.py`
- `scripts/pulse_mux_live_studio_truth_audit.py`
- `reports/pulse_mux_live_publish_pipeline_truth.md`
- `reports/pulse_mux_live_qa_browser_report.md`

## Security

- LiveKit API secret stays server-side.
- Mux stream key stays server-side/host-Studio-only and is masked by default.
- Public viewers receive playback URLs only.
- Only host/admin can request a publisher token.
- LiveKit Egress token is short lived and scoped to `roomRecord`.

## Validation Notes

Static and route audits now verify:

- old preview-only copy is removed,
- the camera overlay does not persist over the active feed,
- LiveKit browser publish code exists,
- backend starts LiveKit egress,
- browser publish no longer requires OBS,
- stream keys are masked by default.

## Required Live QA

Do not consider this production-proven until live infrastructure confirms:

- Start Camera shows only the camera feed, no centered R overlay.
- LiveKit shows a host participant publishing audio/video.
- LiveKit Egress starts and reports an egress ID.
- Mux changes from idle to active.
- Mux bitrate and FPS become greater than zero.
- Public playback works through the Mux HLS URL.

