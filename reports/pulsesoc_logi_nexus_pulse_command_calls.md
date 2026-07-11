# PulseSoc Pulse Command Calls Foundation

Status: native call surface transformed for the Pulse Command vertical.

## Completed

- Reused the existing `CallScreen` implementation instead of creating a duplicate calls surface.
- Preserved the existing call lifecycle handlers:
  - start conversation call
  - accept
  - decline
  - hang up
  - mute / unmute
  - video on / off
  - speaker state
  - camera switch
  - minimize / restore
- Preserved the existing `useNativeCallRoom` media-provider integration and LiveKit/token boundary.
- Preserved the safe web fallback for provider or device cases that native cannot own yet.
- Replaced one-off call screen chrome with shared Pulse Command primitives:
  - `PulseCommandHeader`
  - `PulseCommandPanel`
  - `PulseCommandAvatar`
  - `PulseCommandMetric`
  - `PulseCommandAction`
  - `LogiNexusScrollContainer`
  - `LogiNexusStatePanel`
- Added clearer native readiness visibility for backend call state, token availability, media runtime, and participants.
- Added a LogiNexus empty state for the active-calls surface.

## Reused

- `mobile-native/src/api/calls.ts`
- `mobile-native/src/calls/useNativeCallRoom.ts`
- Existing backend call status, active-call, call event, join-token, and call-control routes.
- Existing provider fallback route.
- Existing Pulse Command design primitives.

## Not Changed

- No LiveKit rewrite.
- No duplicate call business logic.
- No production WebView route changes.
- No Android-specific work.

## Remaining Pulse Command Calls Work

- Populate historical call rows beyond active calls when a read API is available.
- Simulator proof for incoming call state, accepted call state, failed call state, and provider fallback state.
- Physical-device release QA for microphone, camera, Bluetooth, speaker routing, lock-screen ringing, push ringing, and background audio.
- Verify two-device call connection with real provider tokens.

## Completion Estimate

- Calls transformation: 68%.
- Provider boundary clarity: 86%.
- Simulator-verifiable call UI: 76%.
- Physical-device call release confidence: 42%.
