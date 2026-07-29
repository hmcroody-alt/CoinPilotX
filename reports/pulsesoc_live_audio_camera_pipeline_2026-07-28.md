# PulseSoc Live Audio Pipeline + Camera Quality Restoration

Date: 2026-07-28

Branch: `release/undx-nexus-core-v4`

## Root cause

The native Live path had the right high-level LiveKit flow, but the media foundation was not canonical:

- Calls and Live duplicated iOS/LiveKit audio-session setup instead of using one owner-aware realtime media manager.
- Live configured `autoConfigureAudioSession: false`, so every call site had to get AVAudioSession activation, speaker route, Bluetooth/AirPlay options, and cleanup exactly right.
- Live host camera used default LiveKit/React Native WebRTC camera capture behavior. The installed WebRTC defaults are landscape `1280x720`; in a portrait Live stage this can render as a zoomed/cropped host camera.
- Camera enable/flip did not re-verify microphone publication afterward, so camera lifecycle changes could leave the broadcast visually connected while audio was no longer proven.

## Audio architecture decision

Decision: reuse the existing proven call media foundation as the canonical Live foundation.

Reason:

- Native calls already use the correct LiveKit/WebRTC audio profile: `playAndRecord` / `videoChat`.
- Live has the same realtime requirements as calls: microphone capture, remote audio playback, speaker/Bluetooth/AirPlay routing, interruption safety, reconnect, and cleanup.
- A separate Live-only audio engine would duplicate risk and make regressions more likely.

## Files changed

- `mobile-native/src/core/realtimeAudioEngine.ts`
- `mobile-native/src/core/__tests__/realtimeAudioEngine.test.ts`
- `mobile-native/src/calls/useNativeCallRoom.ts`
- `mobile-native/src/live/useLiveBroadcastRoom.ts`
- `scripts/pulsesoc_live_audio_camera_pipeline_audit.py`
- `reports/pulsesoc_live_audio_camera_pipeline_2026-07-28.md`

## Backend changes

None in this pass.

The existing backend LiveKit token and publish-confirmation paths remain authoritative:

- Host/guest/viewer credentials stay server-issued.
- Native host track confirmation still uses the existing native publish route.
- No fake Live state was introduced.

## Media pipeline diagram

```text
Live host
  permissions
  → shared realtime audio engine claims mode live_host/live_guest
  → iOS AVAudioSession playAndRecord/videoChat
  → speaker/Bluetooth/AirPlay route configured
  → LiveKit room connect
  → microphone publish + verify
  → portrait camera publish
  → microphone re-verify after camera
  → backend native-publish confirmation
  → viewers subscribe

Live viewer
  viewer token
  → shared realtime audio engine claims mode live_viewer
  → iOS AVAudioSession playAndRecord/videoChat
  → LiveKit room connect with autoSubscribe
  → remote audio track enablement
  → sound toggle applies to all current/future remote audio tracks
```

## What changed

### Shared realtime audio manager

- Added `RealtimeAudioMode` with `none`, `audio_call`, `video_call`, `live_host`, `live_guest`, `live_viewer`, `voice_message`, and `music_playback`.
- Added owner tracking so only the active media owner releases the audio session.
- Centralized AVAudioSession configuration and output routing.
- Centralized remote audio track enablement.
- Centralized microphone publication verification.

### Live host audio

- Live host now activates the shared realtime audio engine before connecting/publishing.
- Live host publishes microphone first.
- Live host verifies microphone publication before camera and again after camera.
- Camera toggle and camera flip re-verify microphone publication.

### Live viewer audio

- Live viewer uses the same call-grade AVAudioSession profile.
- Remote audio toggle remains separate from local mute and speaker route.
- Remote audio preference is reapplied across track subscription/reconnect.

### Camera quality

- Live now defines portrait capture defaults:
  - front camera
  - `720x1280`
  - `30fps`
  - `9:16` aspect ratio
- Live video publish is bounded with a premium mobile bitrate/framerate contract.
- Live no longer relies on landscape WebRTC defaults for a portrait host stage.

## Tests

Required static/unit checks:

- `npm --prefix mobile-native run typecheck`
- `npm --prefix mobile-native test -- --runTestsByPath src/core/__tests__/realtimeAudioEngine.test.ts src/live/__tests__/liveAudioConfiguration.test.ts src/live/__tests__/remoteAudioReapply.test.ts src/calls/__tests__/useNativeCallRoomAudio.test.ts --runInBand`
- `.venv/bin/python -m py_compile scripts/pulsesoc_live_audio_camera_pipeline_audit.py`
- `.venv/bin/python scripts/pulsesoc_live_audio_camera_pipeline_audit.py`
- `git diff --check -- <mission files>`
- `xcodebuild -workspace mobile-native/ios/PulseSocNative.xcworkspace -scheme PulseSocNative -configuration Debug -destination 'platform=iOS Simulator,id=E859950D-B187-4897-B389-05447C5AD796' -derivedDataPath /tmp/pulsesoc-live-pipeline-sim-20260728 build`
- `xcodebuild -workspace mobile-native/ios/PulseSocNative.xcworkspace -scheme PulseSocNative -configuration Debug -destination 'id=F45E640F-6D02-514E-877C-B764E8D6818F' -derivedDataPath /tmp/pulsesoc-live-pipeline-device-20260728 build`

## Simulator results

- Device: booted iPhone 17 Pro Max simulator `E859950D-B187-4897-B389-05447C5AD796`
- Debug build: passed
- Install: passed
- Launch: passed (`com.pulsesoc.nativeapp.dev`)

## Physical-device results

- Device: connected iPhone 16 Pro `P3r7or` (`F45E640F-6D02-514E-877C-B764E8D6818F`)
- Debug build: passed
- Install: passed
- Launch: passed (`com.pulsesoc.nativeapp.dev`)

Two-device audible Live validation remains required before release signoff:

- Host starts Live on one physical device.
- Viewer joins from a second physical device.
- Required statement for pass: `Viewer heard host audio.`

This environment can push a build to one connected iPhone if available, but cannot honestly prove viewer audio without a second real device/session.

## Remaining risks

- Two physical-device LiveKit media validation is still the release gate.
- `Viewer heard host audio.` has not been proven in this pass because only one physical iPhone was available in this session.
- Bluetooth/headset route changes need physical verification.
- Background/foreground recovery needs physical verification.
- Guest audio needs a real accepted co-host session.
- Low-light camera quality is improved by capture defaults, but not physically measured in this pass.

## Commit SHA

Pending commit.
