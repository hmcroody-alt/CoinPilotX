# PulseSoc Native Calls Practical QA Sweep

Date: 2026-07-18

## Outcome

The native client now consumes the current production WebView call foundation instead of creating a parallel call system. Production Communications V2 remains authoritative for call creation, membership, state, notifications, LiveKit join tokens, controls, connected state, and quality reports.

- Simulator build: PASSED
- Physical iPhone installation: PASSED
- Physical iPhone launch: PASSED
- Development bundle: `com.pulsesoc.nativeapp.dev`
- API environment: `https://pulsesoc.com`
- Production WebView routes were not modified.

## Verified implementation

- Voice and video call entry points exist in Chat and Conversation Control Center.
- Existing production start, accept, decline, end, join-token, status, connected, active-call, event, control, and quality endpoints are reused.
- Native LiveKit uses adaptive streaming, dynacast, simulcast, DTX, RED, echo cancellation, noise suppression, and automatic gain control.
- Local and remote video tracks render in the native Call screen.
- The native audio session performs real speaker/earpiece selection and exposes the system route picker.
- Reconnecting/reconnected, connection quality, track lifecycle, participant lifecycle, and media-device errors are handled.
- Message history and navigation are not replaced by the call layer.
- Background/foreground visibility is synchronized with the backend.
- Minimized calls can be restored or ended through a compact active-call capsule; the capsule is suppressed on the Call screen to prevent duplicate call headers.
- Elapsed duration and a terminal quality summary are submitted without replacing server authority.
- Incoming video-call presentation was exercised in the iPhone 16 Pro simulator.
- A signed Release build was installed and launched on the connected iPhone 16 Pro with the side-by-side development identity.
- User-facing copy does not expose `LogiNexus`.

## Evidence

- Simulator screenshot: `reports/screenshots/native-calls-2026-07-18/incoming-video-working2.png`
- Simulator Xcode build: PASSED
- Physical Xcode Release build: PASSED
- Device install result: bundle `com.pulsesoc.nativeapp.dev` installed successfully
- Device launch result: application launched successfully

## Commands

```text
npm run --prefix mobile-native typecheck
cd mobile-native && EXPO_DOCTOR_ENABLE_DIRECTORY_CHECK=0 npx expo-doctor --verbose
python3 scripts/pulsesoc_native_calls_audit.py
python3 scripts/pulsesoc_native_calls_qa_audit.py
xcodebuild ... -configuration Debug -destination <iPhone 16 Pro simulator>
xcodebuild ... -configuration Release -destination <connected iPhone 16 Pro>
xcrun devicectl device install app ...
xcrun devicectl device process launch ... com.pulsesoc.nativeapp.dev
git diff --check
```

## Remaining release validation

Real two-device media exchange remains a release gate. The next controlled test must place a production-authorized call between two test identities and verify remote audio/video, mute/camera propagation, speaker and Bluetooth routes, interruption recovery, backgrounding, reconnect, decline/end propagation, duration, and quality reporting. CallKit/APNs lock-screen behavior also remains outside this implementation.

No critical, security, data-loss, or production-breaking issue was found in the completed build, installation, launch, routing, or static verification. This does not substitute for the remaining two-device media certification.
