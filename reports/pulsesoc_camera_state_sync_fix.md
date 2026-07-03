# PulseSoc Camera State Synchronization Fix

## Issue

The active video call UI could display `Camera off` in the local picture-in-picture tile while a real video frame was still visible behind the label.

Those states conflict. If a valid video track is present and live, the UI must not display a camera-off placeholder.

## Root Cause

The local PiP fallback and camera button were driven primarily by `state.mutedVideo`, a frontend toggle cache. That cache could drift from the actual LiveKit media state during publish/unpublish, reconnect, foreground recovery, and track events.

The old fallback also rendered as a dark overlay on top of any remaining video element, which made stale states visually obvious.

## Fix

- Added LiveKit-derived camera truth helpers in `static/pulsesoc_calls.js`.
- Local camera state now checks actual local video publications, local tracks, and live video element tracks.
- Remote camera state now checks remote participant video publications, subscription state, mute state, and live video element tracks.
- `Camera off` is blocked when a live video track exists.
- Track `muted`, `unmuted`, `published`, `unpublished`, `subscribed`, and `unsubscribed` paths resync the surfaces.
- When camera-off is true, stale video elements are cleared/removed so frozen frames do not remain behind the placeholder.
- The camera button state is rendered from actual camera truth instead of the cached toggle alone.
- Replaced the black camera-off tile with a PulseSoc avatar/orb fallback with animated Pulse rings.
- Bumped Messenger call CSS/JS cache keys.

## Files Changed

- `static/pulsesoc_calls.js`
- `static/css/pulse_messages_v2.css`
- `templates/pulse_messages_v2.html`
- `scripts/pulsesoc_camera_state_sync_audit.py`
- `reports/pulsesoc_camera_state_sync_fix.md`

## QA Performed

- Static audit verifies the camera state source-of-truth logic.
- JS syntax check verifies the call controller parses.
- Existing call UI and communications V4 audits still pass.

## Remaining Device QA

Real two-device QA should repeat:

- Camera on/off/on loops
- Flip camera
- Background/foreground
- Reconnect
- Poor network recovery

The fix is designed so the UI can only show `Camera off` when LiveKit or the media element confirms there is no active live video track.
