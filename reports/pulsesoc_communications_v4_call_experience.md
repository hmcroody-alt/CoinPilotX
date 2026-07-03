# PulseSoc Communications V4 Call Experience

## Scope

This pass focused on the production call experience now that the LiveKit foundation connects calls:

- Real control behavior for mic, camera, flip, speaker, minimize, and end.
- Stronger call lifecycle cleanup.
- Outgoing and incoming Pulse tones.
- PulseSoc-facing "Pulsing" language across the active call UI.
- Backend call timeline events for user controls beyond mic/camera.
- Safer background/foreground recovery.

Backend APIs, database tables, and notification event types remain internally named around calls. Only the presentation language changed.

## Files Changed

- `static/pulsesoc_calls.js`
- `static/js/pulse_i18n.js`
- `static/js/pulse_messages_v2.js`
- `templates/pulse_messages_v2.html`
- `pulse_communications_v2/routes.py`
- `services/pulsesoc_communications_engine.py`
- `services/pulsesoc_notification_system.py`
- `scripts/pulsesoc_communications_v4_audit.py`
- `reports/pulsesoc_communications_v4_call_experience.md`

## Control Wiring

Mic:
- Mutes/unmutes local LiveKit audio tracks immediately.
- Updates the backend participant state through `mute-audio` / `unmute-audio`.
- Updates the UI state and button label.

Camera:
- Camera Off now unpublishes and stops the local video track instead of leaving a muted/frozen publication.
- Camera On publishes a new video track into the same LiveKit room without reconnecting.
- Backend participant state updates through `disable-video` / `enable-video`.

Flip Camera:
- Uses LiveKit `restartTrack()` when available.
- Falls back to unpublish/republish with the opposite `facingMode`.
- Records `camera_switched` in the call event timeline.

Speaker:
- Uses `HTMLMediaElement.setSinkId()` and `enumerateDevices()` where the browser supports output-device switching.
- Falls back honestly to device-controlled routing where the browser or OS does not expose speaker selection.
- Records `speaker_changed` events for diagnostics.

Minimize:
- Keeps the LiveKit room alive.
- Collapses into the existing floating Pulse bubble.
- Records minimize/restore events without destroying the call.

End:
- Stops tones, quality reporting, status polling, duration timers, control timers, local tracks, remote media elements, and LiveKit room listeners.
- Disconnects the LiveKit room and hides the overlay immediately.
- Sends the backend end request but no longer leaves a stale overlay visible if that request fails.

## Pulsing Language

Visible communication states now use PulseSoc language:

- `Pulsing...`
- `Searching for secure connection...`
- `Waiting for response...`
- `Pulse Accepted`
- `Synchronizing...`
- `Pulse Connected`
- `Excellent Connection`
- `Restoring Pulse...`
- `Missed Pulse`
- `Pulse Declined`
- `Pulse Ended`
- `Pulse Interrupted`

Accessibility labels still use clear call terminology such as "Start audio call", "Incoming voice call", and "End call" so VoiceOver/TalkBack remain understandable.

## Call Tones

The browser client now has lightweight Web Audio tones:

- Outgoing tone: periodic low Pulse tone while waiting.
- Incoming tone: distinct two-tone Pulse ringtone while ringing.
- Tones stop immediately on accept, decline, connect, failure, timeout, or end.
- Browsers may block automatic incoming audio until user interaction; this fails silently and keeps visual ringing intact.

## Background Audio

The client now records background/foreground transitions and tries to restore missing audio/video tracks on foreground.

Known platform reality:

- Browser/PWA background microphone behavior is controlled by iOS/Android/browser policies.
- If the OS suspends microphone capture in the background, web code cannot force it to remain active.
- PulseSoc now detects foreground return and attempts to republish missing tracks instead of silently leaving the user muted.

## Backend Diagnostics

Added authenticated participant-control routes:

- `POST /api/calls/<call_id>/switch-camera`
- `POST /api/calls/<call_id>/speaker`
- `POST /api/calls/<call_id>/minimize`
- `POST /api/calls/<call_id>/restore`
- `POST /api/calls/<call_id>/visibility`

The communications engine records these as call events while preserving participant validation and final-call protection.

## Notification Language

Incoming and missed notifications keep the same internal event types:

- `incoming_call`
- `missed_call`

User-facing text now uses:

- `{Name} is Pulsing You`
- `Voice Connection`
- `Video Connection`
- `Missed Pulse`

## QA Status

Verified locally:

- JS syntax passes for edited call, Messenger, and i18n files.
- Python compile passes for edited backend files.
- Static V4 audit passes.

Not fully verifiable from this desktop-only run:

- iPhone to iPhone background microphone persistence.
- Android to Android output route behavior.
- Bluetooth, headphones, car audio, LTE/5G switching.
- Long-call thermal/battery behavior.

These require real device QA with production LiveKit credentials and two authenticated users.

## Remaining Platform Limits

- True native locked-screen call UI requires iOS CallKit / Android Telecom integration in the native shell.
- Browser speaker switching is only available where `setSinkId()` is supported.
- Background microphone continuity is OS/browser controlled.
- Production-grade packet-loss/FPS/bitrate metrics depend on deeper LiveKit stats collection in a later metrics pass.
