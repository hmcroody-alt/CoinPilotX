# PulseSoc Native Video-Call Audio and In-Call Controls Repair

Date: 2026-07-26

Branch: `codex/video-call-audio-controls-20260726`

Base SHA: `d0d51abfe2700de878c5dfba9620d9dbef420703`

## Result

PARTIAL - implementation is repaired and automated/simulator validation passed, but final PASS requires two physical iPhones with audible bidirectional video-call proof.

No TestFlight build was created or submitted.

## Root Cause

Video publication worked because the native call room enabled camera publication after LiveKit room connection. Audio was weaker because the video-call path depended on optimistic React state rather than confirming local microphone publication and remote audio subscription from LiveKit publications. Camera enable, camera disable, and camera switch also did not reassert the existing microphone publication, so a video path could visually remain connected while audio state was not proven or recovered.

The backend LiveKit token also did not explicitly list microphone and camera publish sources for video calls. While unrestricted publishing may work in some LiveKit configurations, the native contract now declares both sources so token grants match the intended media lifecycle.

## Repair

- Added a shared call media-state normalizer for local microphone publication, local mute state, local video publication, remote audio subscription, remote mute state, and remote video subscription.
- Updated `useNativeCallRoom` to publish microphone before camera for video calls and to re-check microphone publication after camera enable, camera toggle, and camera switch.
- Preserved the existing audio-call iOS audio-session strategy while applying it before video-call media publication.
- Added LiveKit track publication, unpublication, mute, and unmute listeners so the UI reflects SDK state rather than local optimistic state.
- Added a precise video-call audio warning only when a connected video call has missing local audio publication or missing remote audio subscription after a remote participant exists.
- Hid Camera and Flip controls on audio calls; those controls remain video-only while Mute, Speaker, and End remain shared.
- Made video-call LiveKit grants explicitly allow `microphone` and `camera`.

## Final Video-Call Media Lifecycle

```text
permission
-> audio-session activation
-> room connection
-> microphone publication
-> camera publication for video calls
-> microphone publication revalidation
-> remote audio subscription tracking
-> audible playback pending physical two-device proof
```

## Files Changed

- `mobile-native/src/calls/callMediaState.ts` - shared media-state summarization, video-audio warning policy, and native audio routing helpers.
- `mobile-native/src/calls/useNativeCallRoom.ts` - microphone publication enforcement, media-state synchronization, event listeners, audio-session reuse, and camera/mic preservation.
- `mobile-native/src/calls/__tests__/callMediaState.test.ts` - regression tests for local/remote audio/video publication state and audio-session policy.
- `mobile-native/src/screens/CallScreen.tsx` - hides video-only controls during audio calls while preserving shared controls.
- `services/pulsesoc_communications_engine.py` - explicit video-call publish grants for microphone and camera.
- `tests/test_pulsesoc_call_livekit_grants.py` - backend token-grant regression tests.

## Validation

- `npm run --prefix mobile-native typecheck`: PASS.
- Focused native call Jest: PASS, 5 suites / 39 tests.
- Full native Jest: PASS, 90 suites / 1391 tests.
- `cd mobile-native && EXPO_DOCTOR_ENABLE_DIRECTORY_CHECK=0 npx expo-doctor --verbose`: PASS, 16/16.
- Backend LiveKit grant test: PASS, 2 tests.
- Native call audits: PASS.
- Call P0 behavior audit: PASS.
- Incoming calls audit: PASS.
- Fullscreen incoming calls audit: PASS.
- Incoming calls practical QA audit: PASS.
- Native calls practical QA audit: PASS.
- Call system full functionality audit: PASS, 23/23.
- Real call experience audit: PASS, 51/51.
- `xcodebuild` Debug simulator build for iPhone 17 Pro Max: PASS.
- iPhone 17 Pro Max simulator install/launch: PASS.
- Screenshot evidence: `reports/screenshots/native-video-call-audio-2026-07-26/iphone17promax-launch.png`.

## Simulator Observations

The iPhone 17 Pro Max simulator launched the Debug app as the Expo development client shell because Metro was not running. This proves the native target builds, installs, and launches with the repaired call media code path. The simulator cannot prove two-party audible microphone and speaker behavior.

## Physical Device Status

Xcode listed the known physical devices as offline during this mission:

- `P3r7or (18.7.3)`: offline.
- `iPad (3) (26.5.2)`: offline.
- `iPhone (18.1.1)`: offline.
- `iPhone33 (18.6)`: offline.

Physical two-device validation remains required:

- Device 1 calls Device 2 by video.
- Device 1 hears Device 2.
- Device 2 hears Device 1.
- Mute/unmute works both directions.
- Camera toggle preserves microphone.
- Camera flip preserves microphone.
- Speaker routing works.
- End call tears down both sides.
- Repeat with Device 2 calling Device 1.

## Audio-Call Regression Status

The working audio-call path was preserved. The call room still uses the same call-compatible audio-session setup, and audio-call UI no longer exposes video-only camera controls. Focused call tests and existing call audits passed after the repair.

## Final Judgment

PARTIAL - code repair, automated validation, and simulator build/install/launch are complete. Final PASS requires real two-device audible validation.
