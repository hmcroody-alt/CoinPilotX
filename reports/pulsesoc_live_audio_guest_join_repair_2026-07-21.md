# PulseSoc Native Live Audio + Guest Join Repair

Date: 2026-07-21
Branch: `release/undx-nexus-core-v4`
Scope: `mobile-native` Live viewer, Live host, LiveKit room bridge, guest/co-host backend compatibility

## Result

Repository-side Live audio and guest-join blockers were repaired in the native app without replacing the production Live backend.

- Host microphone publishing now configures the native iOS audio session before LiveKit capture.
- Viewer playback now stays on the native LiveKit subscribe path for WebRTC rooms.
- Guest/co-host requests now use the existing production join-request, join-status, token, and publish-complete routes.
- Approved guests now switch from viewer subscription into a native publishing LiveKit connection and confirm audio/video publication with the server.
- Host-side guest approval/mute/unmute/remove remains on the existing native host screen and production APIs.

## Root Causes

1. Host microphone audio could appear absent to viewers because the shared native LiveKit hook registered globals with automatic audio-session configuration disabled, but the native code did not fully own the iOS AVAudioSession before capture. The fix explicitly configures `playAndRecord` + `videoChat` + Bluetooth/AirPlay/speaker options before starting the audio session and before publishing microphone tracks.

2. Native guest join was incomplete. The viewer could watch and request state, but it did not implement the WebView production co-host pipeline after host approval:
   - poll/read join status,
   - request a co-host token,
   - publish camera and microphone,
   - call `/publish-complete`,
   - transition the server guest record to `live`.

3. Native Live had no durable regression audit for the complete audio + guest route set. A new static audit now verifies the host audio configuration, co-host token metadata, production route wrappers, native viewer publish path, host management controls, and unit tests.

## Files Changed

- `mobile-native/src/live/useLiveBroadcastRoom.ts`
- `mobile-native/src/live/liveSession.ts`
- `mobile-native/src/live/__tests__/liveSession.test.ts`
- `mobile-native/src/api/live.ts`
- `mobile-native/src/api/__tests__/live.test.ts`
- `mobile-native/src/screens/LiveScreen.tsx`
- `reports/pulsesoc_native_live_progress.md`
- `reports/pulsesoc_native_progress.md`
- `scripts/pulsesoc_native_live_audit.py`
- `scripts/pulsesoc_live_audio_guest_join_repair_audit.py`

## Backend Compatibility

Reused production routes:

- `POST /api/pulse/live/<live_id>/join`
- `GET /api/pulse/live/<live_id>/state`
- `POST /api/pulse/live/<live_id>/join-request`
- `GET /api/pulse/live/<live_id>/join-status`
- `POST /api/pulse/live/<live_id>/livekit/token`
- `POST /api/pulse/live/<live_id>/guests/<guest_id>/publish-complete`
- `GET /api/pulse/live/<live_id>/join-requests`
- `POST /api/pulse/live/<live_id>/join-requests/<request_id>/<accept|deny>`
- `POST /api/pulse/live/<live_id>/guests/<guest_id>/<mute|unmute|remove>`

No new Live backend, token issuer, room provider, guest state store, moderation path, or viewer-count system was added.

## Audio Publishing Fix

The shared LiveKit hook now configures the native audio session before connecting and publishing:

- iOS category: `playAndRecord`
- iOS mode: `videoChat`
- options: Bluetooth, Bluetooth A2DP, AirPlay, default speaker
- LiveKit room uses echo cancellation, noise suppression, auto gain control, DTX, RED, and simulcast defaults.
- Publish path verifies a local audio publication exists; failure surfaces `LIVE_LOCAL_AUDIO_NOT_PUBLISHED` instead of silently starting a video-only broadcast.

## Guest Join Fix

The native Live viewer now supports:

- Request co-host seat.
- Cancel pending request.
- Read join status and approved guest record.
- Request server-verified co-host token with `role: "cohost"`.
- Publish camera and microphone via the existing LiveKit room hook.
- Confirm published tracks with `/publish-complete`.
- Surface pending, approved, publishing, live, and error states in the native viewer.

## Validation

Automated checks run:

- `npm ci --prefix mobile-native --no-audit --no-fund --progress=false` — PASS
- `npm run --prefix mobile-native typecheck` — PASS
- `npm test --prefix mobile-native -- --runInBand --silent` — PASS (`38` suites, `373` tests)
- `cd mobile-native && EXPO_DOCTOR_ENABLE_DIRECTORY_CHECK=0 npx expo-doctor --verbose` — PASS (`17/17`)
- `.venv/bin/python scripts/pulsesoc_live_audio_guest_join_repair_audit.py` — PASS
- `.venv/bin/python scripts/pulsesoc_native_live_audit.py` — PASS
- `.venv/bin/python scripts/pulsesoc_native_live_webrtc_guest_audio_repair_audit.py` — PASS
- `.venv/bin/python scripts/pulsesoc_native_live_device_qa_audit.py` — PASS
- `.venv/bin/python scripts/pulsesoc_native_webview_replacement_audit.py` — PASS (`0` hard web-exit blockers)
- `git diff --check` — PASS

Device validation completed in this mission:

- Xcode iPhone Simulator build: PASS (`iPhone 17 Pro Max`, `E859950D-B187-4897-B389-05447C5AD796`)
- Xcode iPhone Simulator install/launch: PASS (`com.pulsesoc.nativeapp.dev`)
- Xcode iPhone Simulator screenshot: PASS (`reports/screenshots/live-audio-guest-join-repair-2026-07-21/iphone-17-pro-max-launch.png`)
- Physical iPhone build/install/launch: PASS (`F45E640F-6D02-514E-877C-B764E8D6818F`, dev sidecar `com.pulsesoc.nativeapp.dev`)

## Physical QA Evidence

Current repository-side status:

- Host audio publishing path: code-path verified.
- Guest join/publish path: code-path verified.
- Xcode Simulator: build/install/launch/screenshot verified.
- Physical iPhone install/launch: verified on the dev sidecar app.
- Host iPhone + Guest iPhone + Viewer iPhone end-to-end audio/video: NOT OBSERVED.
- Bluetooth/headphones/AirPods/speaker route verification: NOT OBSERVED.
- Background/foreground interruption with real participants: NOT OBSERVED.

These remain release QA blockers until directly observed with real accounts/devices.

## Remaining Limitations

- The simulator cannot prove real microphone capture quality, Bluetooth routing, or multi-device audience audio.
- A single physical iPhone cannot prove host/guest/viewer end-to-end audio. Minimum matrix remains Host iPhone + Guest iPhone + Viewer iPhone.
- Production LiveKit/TURN/ICE health must be validated in the configured production environment.
