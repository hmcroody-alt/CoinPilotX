# PulseSoc Real Call Experience - Phase 2

## What Phase 1 Already Provides

- Central call state service in `services/pulsesoc_communications_engine.py`
- Call tables for calls, participants, events, quality reports, and device sessions
- LiveKit token generation with explicit `config_missing` behavior
- Incoming and missed call notification hooks through the central notification system
- Messenger and Conversation Control Center call buttons wired to `window.PulseSocCalls`
- Basic call shell/minimized pill UI

## What Was Missing

- Messenger did not load the LiveKit browser bundle.
- `static/pulsesoc_calls.js` only displayed status text; it did not connect to a LiveKit room, publish mic/camera tracks, subscribe to remote tracks, or handle incoming calls.
- The backend lacked Phase 2 routes for client-connected state, call events, conversation call history, and participant media controls.
- Missing-provider errors exposed provider wording to users instead of a clean calling-unavailable message.
- Stale ringing cleanup was not tied into active/status polling.

## What Was Built

- Real browser call controller in `static/pulsesoc_calls.js`
  - Starts audio/video calls through `/api/calls/start`
  - Connects to LiveKit when `LIVEKIT_URL`, `LIVEKIT_API_KEY`, and `LIVEKIT_API_SECRET` are configured
  - Publishes microphone tracks for audio calls
  - Publishes microphone and camera tracks for video calls
  - Subscribes to remote audio/video tracks
  - Shows incoming, outgoing ringing, active, reconnecting, declined, missed, ended, and failed states
  - Supports accept, decline, end, minimize/restore, mute/unmute, camera on/off, camera flip where supported, speaker-safe fallback, and quality reports
  - Polls active calls so in-app incoming calls can appear without a page refresh
  - Handles `?call_id=` deep links from push notifications

- Backend Phase 2 additions
  - `POST /api/calls/<call_id>/connected`
  - `GET /api/calls/<call_id>/events`
  - `GET /api/conversations/<conversation_id>/calls`
  - `POST /api/calls/<call_id>/mute-audio`
  - `POST /api/calls/<call_id>/unmute-audio`
  - `POST /api/calls/<call_id>/enable-video`
  - `POST /api/calls/<call_id>/disable-video`
  - `POST /api/calls/<call_id>/screen-share/start`
  - `POST /api/calls/<call_id>/screen-share/stop`

- UI/CSS
  - Full PulseSoc-native call overlay
  - Mobile-safe incoming call screen with accept/decline controls
  - Video stage with remote media and local preview
  - Active call controls and minimized call pill
  - Reduced visual clutter and no default browser dialogs

## LiveKit Integration Status

When LiveKit config exists, the browser client loads `/static/vendor/livekit-client.umd.js`, creates a LiveKit `Room`, connects with the server-issued token, publishes local tracks, and renders subscribed remote tracks.

When config is missing, the backend returns:

```json
{
  "ok": false,
  "status": "config_missing",
  "message": "Calling is temporarily unavailable. Please try again later."
}
```

The frontend shows that safe message and does not pretend the call started.

## Notification Integration

Incoming calls continue to create urgent central notification events with:

- `type = incoming_call`
- `category = calls`
- `priority = urgent`
- `sound_key = call`
- vibration metadata
- deep link to the Messenger conversation with `call_id`

Missed calls are generated when stale ringing calls exceed the configured timeout and are routed through the existing `notify_missed_call` helper.

## Call History Behavior

Phase 2 adds a secure call history API through `GET /api/conversations/<conversation_id>/calls`. Full timeline rendering as chat system messages remains a Phase 3 UI task.

## Quality Indicator Behavior

The UI reports simple states: Ready, Ringing, Connecting, Good, Reconnecting, Disconnected, and Offline. The browser submits throttled quality reports every 30 seconds while a call is active.

## Security Checks

- Every call route requires authentication.
- Users must be call/conversation participants to read status, events, history, generate tokens, or change call controls.
- LiveKit token identity is tied to the authenticated user id.
- LiveKit secrets remain server-side and are not present in frontend files.
- Webhook verification remains secret-gated.
- Calls blocked by relationship/conversation blocks are rejected before notifications.

## Mobile QA

Static mobile-safe checks passed:

- Incoming overlay uses safe-area padding.
- Buttons are large touch targets.
- Bottom dock does not cover the call sheet because the overlay pads above the dock.
- No horizontal overflow is introduced by the call shell CSS.

Browser two-user media QA was not completed in this environment because confirmed LiveKit credentials are not present locally.

## Desktop QA

Static desktop checks passed:

- LiveKit bundle loads before `pulsesoc_calls.js`.
- Header and Conversation Control Center buttons call the same `PulseSocCalls` service.
- The call panel centers on desktop and supports minimized restore.

## Known Limitations

- True iOS CallKit / Android Telecom incoming-call UI is future native-app work.
- Screen sharing is only safely prepared through backend state; full display-track publishing is deferred.
- Conversation timeline call history rendering is not yet fully integrated.
- Group calls use the same room foundation, but advanced group call controls are Phase 3.
- Real two-user audio/video QA requires valid LiveKit credentials and two authenticated sessions.

## Missing Provider Requirements

Required for real calls:

- `LIVEKIT_URL`
- `LIVEKIT_API_KEY`
- `LIVEKIT_API_SECRET`
- `LIVEKIT_WEBHOOK_SECRET`

Optional network support:

- `TURN_SERVER_URL`
- `TURN_USERNAME`
- `TURN_PASSWORD`
- `STUN_SERVER_URL`

## Verification

Targeted verification was run during implementation:

- `venv/bin/python -m py_compile services/pulsesoc_communications_engine.py pulse_communications_v2/routes.py`
- `node --check static/pulsesoc_calls.js`
- `node --check static/js/pulse_messages_v2.js`

Full final verification is tracked by `scripts/pulsesoc_real_call_experience_audit.py`.
