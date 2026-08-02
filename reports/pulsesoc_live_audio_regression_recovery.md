# PulseSoc Live Audio Regression Recovery

Date: 2026-08-02
Branch: `codex/store-dashboard-live`
Starting SHA: `8f99e54235ffd9954fc23af6b90bb4b5d5d82075`

## Incident

Physical testing reported that Live broadcast startup failed with:

`The native real-time audio engine did not remain active.`

This is the fail-closed guard from the shared real-time audio engine. The guard was intentionally preserved; the fix does not remove, weaken, or mask it.

## Baseline Comparison

Verified stable reference: `realtime-audio-stable-v1`

The latest media-quality layer introduced a route where Live host/co-host sessions could leave the physically verified Live baseline when the existing `live_elite_audio_enabled` or `live_elite_video_enabled` flags were present. That meant a production or QA token could alter Live publisher room/capture/publish defaults before the host camera/microphone path had been revalidated on device.

Calls continue to use their known-good shared real-time audio engine. The emergency recovery therefore restores Live publisher sessions to the stable baseline unless the server sends a new, explicit `live_publisher_quality_enabled` flag.

## Root Cause

Root cause category: media-quality rollout guard was too broad for Live publishers.

Specific cause:
- `live_host` and `live_guest` treated existing Live elite audio/video flags as enough to move away from the verified baseline.
- That could apply quality changes to the same startup window where LiveKit camera startup and iOS RemoteIO stabilization are most sensitive.
- The real-time audio engine guard correctly detected that the native engine did not remain active after startup and failed closed.

## Repair

Implemented an explicit Live-publisher quality gate:

- Added `livePublisherQualityEnabled` / `live_publisher_quality_enabled`.
- Default is `false`.
- Existing `live_elite_audio_enabled` and `live_elite_video_enabled` no longer affect `live_host` or `live_guest` unless this new publisher gate is also explicitly true.
- The verified stable Live baseline remains unchanged.
- The guard error remains active and will still fail loudly if the native engine stops.

## Files Changed

- `mobile-native/src/core/mediaQualityPolicy.ts`
  - Added the Live-publisher opt-in field and required it for Live host/co-host quality upgrades.
- `mobile-native/src/core/mediaQualityFlags.ts`
  - Added parsing, defaults, wire alias, and telemetry descriptor for the new server flag.
- `mobile-native/src/core/__tests__/mediaQualityPolicy.test.ts`
  - Added regression coverage proving Live publishers stay baseline without explicit publisher opt-in.
- `mobile-native/src/core/__tests__/mediaQualityWiring.test.ts`
  - Added wiring coverage for legacy Live elite flags without the new publisher gate.

## Validation

Focused automated validation:

```text
npm test -- --runInBand \
  mobile-native/src/core/__tests__/mediaQualityPolicy.test.ts \
  mobile-native/src/core/__tests__/mediaQualityWiring.test.ts \
  mobile-native/src/live/__tests__/liveAudioConfiguration.test.ts
```

Result:

```text
PASS src/core/__tests__/mediaQualityPolicy.test.ts
PASS src/core/__tests__/mediaQualityWiring.test.ts
PASS src/live/__tests__/liveAudioConfiguration.test.ts
Test Suites: 3 passed, 3 total
Tests: 108 passed, 108 total
```

Full TypeScript validation:

```text
npx tsc --noEmit
```

Result: blocked by pre-existing unrelated Business OS/activity dirty work:

```text
src/screens/ActivityRoute.tsx(9,32): Cannot find module './ActivityScreen'
src/screens/EventsManagerScreen.tsx: "events" / "live" / "advertising" are not assignable to NativeSyncSubsystem
```

Those files are unrelated to Live audio and were not changed by this recovery.

## Physical QA

Not completed in this run.

Required before declaring PASS:

- Start Live as host on physical iPhone.
- Verify no `REALTIME_AUDIO_ENGINE_INACTIVE` failure.
- Verify host microphone remains active after camera startup.
- Join from a second physical device.
- Confirm viewer hears host audio.
- Confirm guest request and co-host publish path remain stable.

## Final Judgment

PARTIAL — the regression path was isolated and repaired in code, and focused tests pass. Full validation is blocked by unrelated dirty TypeScript errors, and physical two-device Live audio proof remains required.
