# PulseSoc Native Calls Foundation Progress

Updated: 2026-07-18

## Scope

Native LiveKit Calls foundation was added as a parallel native client surface for `com.pulsesoc.nativeapp`. The production WebView app and production PulseSoc call backend remain authoritative and untouched.

## Reuse-First Inventory

Existing PulseSoc backend/API behavior reused:

- `POST /api/pulse/communications/v2/conversations/<conversation_ref>/voice/start`
- `POST /api/pulse/communications/v2/conversations/<conversation_ref>/video/start`
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
- Reused the production LiveKit connection contract while adding native adaptive stream, dynacast, simulcast, DTX, RED, echo cancellation, noise suppression, and automatic gain control.
- Wired the native iOS audio session to real speaker/earpiece selection, with the native route picker available from a long press.
- Added provider reconnect/reconnected handling, connection-quality state, media-device errors, participant/track lifecycle handling, and local/remote camera track state.
- Added `mobile-native/src/screens/CallScreen.tsx` with:
  - active-call list fallback,
  - conversation call start,
  - incoming call accept/decline,
  - end call,
  - LiveKit join-token connection and local/remote video rendering,
  - mute/unmute,
  - camera enable/disable,
  - real speaker/earpiece routing,
  - camera switch,
  - minimize/restore,
  - automatic reconnect state and secure media-resume feedback,
  - elapsed call duration and quality reporting,
  - background/foreground visibility synchronization,
  - call state refresh,
  - safe web fallback.
- Added voice/video buttons to native Chat and its Conversation Control Center without changing the production WebView Messenger.
- Removed the global active-call restore capsule entirely. Legitimate call controls live only on the dedicated Call screen; the global layer is incoming-ringing-only.
- Added server-authoritative stale non-ringing call expiry and current-participant validation so locally cached metadata cannot prove an active call.
- Corrected native conversation call creation to the production `/api/pulse/communications/v2` call routes and removed the misleading global upload-specific 404 copy.
- Compacted the dedicated Call layout and keyboard-aware Messenger composer without manual keyboard-height offsets.
- Added configuration-specific native display names so Debug/dev installs read `PulseSoc Native Dev` while Release remains `PulseSoc Native` unless a device-QA build explicitly overrides it.
- Added native route and deep-link support for `/pulse/calls/<call_id>`.
- Routed existing message notification links containing `call_id` into the native Call screen.

## QA Status

Verified on 2026-07-18:

- `npm run --prefix mobile-native typecheck`
- `cd mobile-native && EXPO_DOCTOR_ENABLE_DIRECTORY_CHECK=0 npx expo-doctor --verbose`
- `venv/bin/python scripts/pulsesoc_native_calls_audit.py`
- `git diff --check`
- iPhone 16 Pro simulator Debug build
- Signed Release build for the connected iPhone 16 Pro
- Side-by-side physical installation as `com.pulsesoc.nativeapp.dev`
- Physical launch with the production API base URL
- Simulator incoming video-call route and full-screen call presentation
- Simulator cold launch after a QA URL, proving no incoming/active call survives process restart without a canonical server call
- Compact `PULSE LINK` composer with bounded multiline growth and no dead keyboard gap
- Behavior-level stale-call cleanup using the real Communications engine against an isolated database

Not yet claimed:

- Real two-user LiveKit audio/video exchange and quality measurement.
- APNs/FCM incoming-call lock-screen behavior.
- Bluetooth route controls.
- Background audio continuity.
- Real two-device incoming/outgoing call handoff.
- End-to-end native camera/microphone permission acceptance during a real second-party call.

The production-backed foundation is installed and usable for device QA. Real two-device media exchange remains a release gate; it is not falsely claimed by a one-device build or synthetic incoming-call screen.

## Risk

Risk level: high.

Reason: calls combine LiveKit media, device audio sessions, camera/microphone permissions, push routing, background behavior, and server call state. This foundation intentionally keeps the server authoritative and limits native logic to UI, device connection, and control routing.

## Recommended Next Action

Run a controlled two-account, two-device call: create from WebView, answer in native, verify remote audio/video, speaker/Bluetooth routing, background/foreground recovery, reconnect, decline/end propagation, duration, and quality submission. Complete CallKit/APNs lock-screen certification before any production replacement or App Store submission.
