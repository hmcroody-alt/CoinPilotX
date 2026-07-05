# PulseSoc Native Calls Foundation Progress

## Scope

Native LiveKit Calls foundation was added as a parallel native client surface for `com.pulsesoc.nativeapp`. The production WebView app and production PulseSoc call backend remain authoritative and untouched.

## Reuse-First Inventory

Existing PulseSoc backend/API behavior reused:

- `POST /api/pulse/comm/v2/conversations/<conversation_ref>/voice/start`
- `POST /api/pulse/comm/v2/conversations/<conversation_ref>/video/start`
- `POST /api/calls/start`
- `POST /api/calls/<call_id>/accept`
- `POST /api/calls/<call_id>/ring-seen`
- `POST /api/calls/<call_id>/decline`
- `POST /api/calls/<call_id>/end`
- `POST /api/calls/<call_id>/join-token`
- `GET /api/calls/<call_id>/status`
- `GET /api/calls/active`
- `POST /api/calls/<call_id>/quality`
- `POST /api/calls/<call_id>/connected`
- `GET /api/calls/<call_id>/events`
- `GET /api/conversations/<conversation_ref>/calls`
- Native control routes for mute, unmute, video enable/disable, camera switch, speaker, minimize, restore, and visibility.

Existing backend systems kept authoritative:

- Communications V2 call engine.
- LiveKit token generation.
- LiveKit room naming and provider readiness.
- Participant authorization and conversation membership checks.
- Call state, call events, device sessions, and quality reporting tables.
- Call notification delivery and deep-link payloads.
- Existing call moderation, eligibility, and server-side permissions.

Native components/services reused:

- Existing native navigation stack.
- Existing Messenger conversation route.
- Existing notification/deep-link routing.
- Existing cache helpers.
- Existing `pulseApi` session/cookie handling.
- Existing design tokens and loading/error patterns.

## Implemented

- Added `mobile-native/src/api/calls.ts` as a typed API wrapper over the existing PulseSoc call endpoints.
- Added `mobile-native/src/calls/useNativeCallRoom.ts` for native-only LiveKit room connection, guarded so QA browser/web receives safe fallback behavior.
- Added `mobile-native/src/screens/CallScreen.tsx` with:
  - active-call list fallback,
  - conversation call start,
  - incoming call accept/decline,
  - end call,
  - LiveKit join-token connection shell,
  - mute/unmute,
  - camera enable/disable,
  - speaker state,
  - camera switch,
  - minimize/restore,
  - call state refresh,
  - call event display,
  - safe web fallback.
- Added voice/video buttons to native Chat without changing the production WebView Messenger.
- Added native route and deep-link support for `/pulse/calls/<call_id>`.
- Routed existing message notification links containing `call_id` into the native Call screen.

## QA Status

Static verification is required before merge/commit:

- `npm run --prefix mobile-native typecheck`
- `cd mobile-native && EXPO_DOCTOR_ENABLE_DIRECTORY_CHECK=0 npx expo-doctor --verbose`
- `venv/bin/python scripts/pulsesoc_native_calls_audit.py`
- `git diff --check`

Not yet claimed:

- Physical iPhone/Android LiveKit audio/video quality.
- APNs/FCM incoming-call lock-screen behavior.
- Bluetooth route controls.
- Background audio continuity.
- Real two-device incoming/outgoing call handoff.
- Native camera/microphone call permissions on physical devices.

These remain release blockers, not development blockers.

## Risk

Risk level: high.

Reason: calls combine LiveKit media, device audio sessions, camera/microphone permissions, push routing, background behavior, and server call state. This foundation intentionally keeps the server authoritative and limits native logic to UI, device connection, and control routing.

## Recommended Next Action

Run a practical QA browser/static pass for call routing and then continue with the next highest-value native implementation. Do not block development on full physical call QA yet. Full two-device iOS/Android LiveKit call QA should be scheduled before any production replacement or App Store submission.
