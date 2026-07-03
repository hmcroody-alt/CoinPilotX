# PulseSoc Video Call Camera Sync Fix

## Root Cause

The local picture-in-picture camera state could drift from the real media state because the camera button used cached `state.mutedVideo` to decide the next action, while the rendered surface separately inferred camera state. The local camera-off placeholder was also translucent, so the full-screen remote video could bleed through the card and look like a ghost/stale local frame.

## What Changed

- Camera truth now derives from LiveKit participant/publication state, local video publications, live media tracks, and attached video state.
- `participant.isCameraEnabled === false` now overrides stale DOM video.
- The camera button now calls `syncLocalCameraSurface()` before deciding whether the next tap should turn camera off or on.
- Camera off now disables the LiveKit camera, unpublishes/stops local video tracks, detaches the local preview, clears `srcObject`, removes stale source data, and marks the preview as `data-camera-state="off"`.
- Camera on republishes a local video track, reattaches the preview, and marks the preview as `data-camera-state="live"`.
- The local camera-off card is now opaque and explicitly hides the local video element, so no stale frame or remote background can show through.
- Backend participant state remains wired through existing `disable-video` and `enable-video` controls.

## Files Changed

- `static/pulsesoc_calls.js`
- `static/css/pulse_messages_v2.css`
- `scripts/pulsesoc_call_camera_sync_audit.py`
- `reports/pulsesoc_call_camera_sync_fix.md`

## Expected Behavior

- Camera off shows only the PulseSoc avatar/profile placeholder and `Camera off`.
- No hidden/faint video remains behind the placeholder.
- Camera on immediately removes the placeholder and restores the local preview.
- The remote participant receives the actual LiveKit video unpublish/republish behavior.
- Button text and visual state follow actual camera truth rather than stale UI state.

## QA Results

- `node --check static/pulsesoc_calls.js` passed.
- `node --check static/js/pulse_messages_v2.js` passed.
- `venv/bin/python -m py_compile scripts/pulsesoc_call_camera_sync_audit.py` passed.
- `venv/bin/python scripts/pulsesoc_call_camera_sync_audit.py` passed.

## Remaining Manual QA

A real two-device active video call should be used to repeat:

- camera on -> off -> on -> off
- flip camera -> off -> on
- minimize -> restore
- background -> foreground
- network reconnect
- end call -> start new video call

The expected result is no stale `Camera off` overlay while a valid local camera track is rendering, and no ghost video behind the off placeholder.
