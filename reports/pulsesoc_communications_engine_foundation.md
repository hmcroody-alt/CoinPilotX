# PulseSoc Communications Engine Foundation

## What Was Added

Built the Phase 1 foundation for PulseSoc audio/video calls:

- Central call service: `services/pulsesoc_communications_engine.py`
- PostgreSQL-compatible migration: `migrations/pulsesoc_communications_engine.sql`
- Exact API routes for call start, accept, decline, end, join token, status, active calls, quality telemetry, LiveKit webhook, and admin diagnostics
- Messenger frontend call service skeleton: `static/pulsesoc_calls.js`
- Messenger header audio/video buttons wired to the central service
- Conversation Control Center quick actions wired to the same call service
- Call overlay/minimized pill UI foundation
- Static audit: `scripts/pulsesoc_communications_engine_audit.py`

## Tables Added

The migration defines:

- `communication_calls`
- `communication_call_participants`
- `communication_call_events`
- `communication_call_quality_reports`
- `communication_call_device_sessions`

Runtime schema creation is SQLite-safe for local development while the migration remains PostgreSQL-compatible for production.

## Routes Added

- `POST /api/calls/start`
- `POST /api/calls/<call_id>/accept`
- `POST /api/calls/<call_id>/decline`
- `POST /api/calls/<call_id>/end`
- `POST /api/calls/<call_id>/join-token`
- `GET /api/calls/<call_id>/status`
- `GET /api/calls/active`
- `POST /api/calls/<call_id>/quality`
- `POST /api/pulse/communications/v2/livekit/webhook` (legacy Communications HMAC adapter)

The standard LiveKit provider webhook remains canonically owned by
`POST /api/livekit/webhook`; the Communications adapter must not shadow that
route because provider events use LiveKit's signed Authorization token.
- `GET /api/admin/calls/recent`
- `GET /api/admin/calls/<call_id>`
- `POST /api/admin/calls/test-config`

Existing Messenger routes now use the same engine:

- `POST /api/pulse/communications/v2/conversations/<conversation_ref>/voice/start`
- `POST /api/pulse/communications/v2/conversations/<conversation_ref>/video/start`

## LiveKit Readiness

Required environment variables are audited and supported:

- `LIVEKIT_URL`
- `LIVEKIT_API_KEY`
- `LIVEKIT_API_SECRET`
- `LIVEKIT_WEBHOOK_SECRET`

If LiveKit is not configured, call start returns `config_missing` and does not fake a ringing or connected call.

## Notification Integration

The engine connects to the central notification system for:

- `incoming_call`
- `missed_call`

Incoming calls use urgent call notifications with in-app/push/call delivery eligibility. Muted conversations suppress noisy push delivery where participant settings require it. Missed calls use the existing central `notify_missed_call` helper.

## Frontend Integration

`static/pulsesoc_calls.js` exposes:

- `startAudioCall()`
- `startVideoCall()`
- `acceptCall()`
- `declineCall()`
- `endCall()`
- `joinCallRoom()`
- `toggleMicrophone()`
- `toggleCamera()`
- `switchCamera()`
- `switchSpeaker()`
- `submitQualityReport()`

The frontend checks microphone/camera support and permission before requesting a call. Permission-check streams are stopped immediately to avoid leaking media tracks.

## Security Checks

- Auth required for all user call routes
- Conversation participant validation before starting or joining calls
- Recipient validation against active conversation members
- Self-calls blocked
- Blocked conversations blocked
- LiveKit token identity tied to authenticated user id
- LiveKit secrets stay server-side
- Webhook processing requires `LIVEKIT_WEBHOOK_SECRET`
- Admin diagnostics require admin access

## QA Results

Static and syntax verification passed for the implemented foundation:

- Python compile checks
- JavaScript syntax checks
- Communications audit
- `git diff --check`
- Local `/health` check

Browser-level real call join was not performed because LiveKit credentials are not configured in the local environment. The UI now presents a clear provider configuration state instead of fake call success.

## Missing Env Vars

The local environment does not expose confirmed LiveKit credentials. Production calling requires:

- `LIVEKIT_URL`
- `LIVEKIT_API_KEY`
- `LIVEKIT_API_SECRET`
- `LIVEKIT_WEBHOOK_SECRET`

Optional future network config:

- `TURN_SERVER_URL`
- `TURN_USERNAME`
- `TURN_PASSWORD`
- `STUN_SERVER_URL`

## Phase 2

Phase 2 should add:

- Real LiveKit browser client connection
- Full active call screen
- Locked-screen incoming call ringing path
- Group calls
- Screen sharing
- Background reconnect
- Call history UI
- Call cleanup worker scheduling
- Quality auto-adaptation
