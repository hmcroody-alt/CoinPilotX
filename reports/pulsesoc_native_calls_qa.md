# PulseSoc Native Calls Practical QA Sweep

Date: 2026-07-05

## Scope

This was a short practical QA sweep for the Native Calls foundation. It was not a full two-device call certification pass.

Goal: keep development moving while catching critical routing, UI, fallback, and production-safety issues.

## Verification Summary

Passed:

- Native Messenger voice call button is present in `ChatScreen`.
- Native Messenger video call button is present in `ChatScreen`.
- Chat call buttons navigate to the native `Call` route with conversation ID and call type.
- Call API wrapper uses existing PulseSoc backend routes instead of duplicating call business logic.
- `/pulse/calls/:callId` native route is registered in React Navigation linking.
- `/pulse/calls/test-call` returned `200 OK` from `npm run web:qa` on `localhost:8094`.
- Call screen renders loading, active-call, start-call, incoming-call, error, and fallback states.
- Accept, decline, end, mute, video, speaker, flip, minimize, restore, and safe fallback controls are present.
- LiveKit native runtime is guarded behind `Platform.OS !== "web"` and dynamic imports.
- Web/QA browser fallback is explicit for unsupported native LiveKit behavior.
- Notification routing handles `/pulse/calls/<call_id>`.
- Existing message notification links with `call_id` route to the native Call screen.
- Production WebView routes were not modified.
- User-facing copy does not expose `LogiNexus`; design standard remains internal.

## Commands Run

```text
npm ci --prefix mobile-native --no-audit --no-fund --progress=false
npm run --prefix mobile-native typecheck
cd mobile-native && EXPO_DOCTOR_ENABLE_DIRECTORY_CHECK=0 npx expo-doctor --verbose
venv/bin/python scripts/pulsesoc_native_calls_audit.py
venv/bin/python scripts/pulsesoc_native_calls_qa_audit.py
git diff --check
npm run web:qa
curl -sS -I http://localhost:8094/pulse/calls/test-call
```

## Web Route Evidence

`curl -I http://localhost:8094/pulse/calls/test-call` returned:

```text
HTTP/1.1 200 OK
Content-Type: text/html
```

This verifies the Expo web server can serve the Calls deep-link path. It does not prove authenticated call state, LiveKit media, or two-device call behavior.

## Not Verified In This Sweep

- Authenticated browser click-through into a real conversation call.
- Real backend call creation with a QA conversation ID.
- LiveKit audio/video connection on iOS or Android.
- Incoming-call push notification delivery.
- Lock-screen call behavior.
- Bluetooth/speaker route behavior.
- Background audio continuity.
- Physical-device camera/microphone permission prompts for calls.
- Two-device accept/decline/end timing.

These are release blockers, not current development blockers unless they expose a critical/security/data-loss/production-breaking issue.

## Issues Found

No critical, security, data-loss, or production-breaking issues were found in this sweep.

## Recommendation

Continue development. The next highest-value action is to keep building the next native feature while scheduling a later focused Calls release-readiness pass for two-device LiveKit media, APNs/FCM call notifications, lock-screen behavior, and background audio.
