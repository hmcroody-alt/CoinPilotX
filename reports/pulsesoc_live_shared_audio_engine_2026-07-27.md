# PulseSoc Live Shared Audio Engine Repair — 2026-07-27

## Scope

PulseSoc Live now reuses the proven native call audio mechanics without routing
Live through a call room or CallKit. Live retains its own LiveKit room identity,
host/viewer/co-host grants, participant lifecycle, and UI.

No TestFlight build was created or submitted.

## Exact pre-change divergence

| Stage | Working call path | Live path before refactor | Shared behavior after refactor |
|---|---|---|---|
| iOS audio session | `playAndRecord` + `videoChat`, speaker default, Bluetooth/AirPlay options | Duplicated role-specific setup | `realtimeAudioEngine` owns activation; publishers use the call profile, listeners use playback without microphone permission |
| Microphone capture | Enables microphone and retries publication with off/on recovery | One enable followed by one delayed count | Shared verified publication and retry |
| Remote playback | Drives every subscribed audio track enabled after subscribe/connect | Only reapplied when viewer sound was off | Shared desired state applied for sound on and off |
| Reconnect | Republishes microphone and reapplies remote audio | Did not republish host/guest microphone | Shared room-audio restoration preserves desired host/guest mute and viewer sound state |
| Mute/unmute | Verifies real publication | Updated Live UI after SDK call without publication verification | Shared verified microphone control |
| Camera operations | Camera toggle/flip assert microphone continuity | No microphone continuity assertion | Live now performs the same publication assertion |
| Routing | Shared native speaker/earpiece/Bluetooth route picker semantics | Duplicated route code | Shared routing functions |
| Foreground recovery | Call media remains protected by call lifecycle/ownership | Non-CallKit Live had no explicit audio-session resume | Live resumes the shared engine and restores desired room audio on app activation |
| Cleanup | Disconnect plus audio-session stop | Duplicated cleanup | Shared audio-session stop |

## Shared lifecycle

`audio-session activation → LiveKit room join → microphone permission/capture → verified local publication → remote subscription → enabled native playback → routing → interruption recovery → cleanup`

The shared engine does not own tokens, room names, host/co-host authorization,
guest approval, participant rendering, comments, or guest sheets.

## Files

- `mobile-native/src/core/realtimeAudioEngine.ts` — shared native audio session,
  capture/publication, subscription/playback, routing, recovery, and cleanup.
- `mobile-native/src/core/__tests__/realtimeAudioEngine.test.ts` — shared-engine
  regression coverage.
- `mobile-native/src/calls/useNativeCallRoom.ts` — adopts the extracted engine
  without changing call identity or CallKit behavior.
- `mobile-native/src/live/useLiveBroadcastRoom.ts` — adopts the engine while
  preserving Live roles, room credentials, participants, and UI state.
- `scripts/pulsesoc_live_audio_guest_join_repair_audit.py` — verifies that calls
  and Live use the common engine and that Live guest contracts remain present.

## Evidence boundary

Static tests, repository audits, and simulator compilation can verify wiring and
regression behavior. They cannot prove audible sound. Physical paired-device
proof remains required for:

1. Host microphone publication.
2. Viewer hearing the host.
3. Host mute/unmute.
4. Viewer sound on/off.
5. Comments and guest sheets preserving playback.
6. Guest publication audible to host and viewers.
7. Audio-call regression.

## Validation completed

- TypeScript: passed.
- Focused shared/call/Live audio tests: 4 suites, 22 tests passed.
- Full native Jest: 100 suites, 1,700 tests passed.
- `npm run verify`: passed, 100 suites and 1,700 tests.
- Expo Doctor: 16/16 checks passed after a clean worktree dependency install.
- Live audio/video, Live guest, native Live viewer, and Live join-flow audits:
  passed.
- Call system audit: 23/23.
- Real-call experience audit: 51/51.
- Native call, P0 behavior, practical QA, audio-call, and video-call audits:
  passed.
- iPhone 17 Pro Max simulator build: passed.
- Signed iPhoneOS debug build for the separate
  `com.pulsesoc.nativeapp.dev` bundle: passed.
- Physical iPhone 16 Pro installation: passed; local development bundle
  version `1.0.1 (5)`.

## Physical-device blocker

Only one paired physical iPhone was available. The refactored development app
installed and requested its Metro bundle, but failed before app registration
with:

`TypeError: NativeJSLogger.default.addListener is not a function`

That native development-runtime mismatch is outside this shared Live/call audio
scope. PulseSoc Live never mounted, so no microphone, subscription, routing, or
audible behavior was claimed from this launch.

Physical result:

1. Host starts Live and microphone publishes: not observed.
2. Viewer joins and hears the host: not observed.
3. Host mute/unmute: not observed.
4. Viewer sound on/off: not observed.
5. Comments and guest sheets preserve playback: code-path verified; not
   physically observed.
6. Approved guest publishes audible audio: not observed.
7. Working audio calls remain unaffected: static tests and audits passed; not
   physically observed after the refactor.

Final evidence classification: **PARTIAL**.
