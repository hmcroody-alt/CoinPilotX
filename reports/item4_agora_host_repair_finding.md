# Item 4 — Agora-only host audio repair coverage + telemetry

**Verdict: STOP AND REPORT. No code was changed.**

Item 4 asked for two things to be preserved from
`claude/trusting-neumann-381eb0`: the `audio_engine_playout_init_failed`
telemetry event, and the `liveHostEngineRepair.test.ts` coverage. Neither can be
preserved onto this integration HEAD without either (a) reviving the LiveKit
WebRTC audio device module, which the directive's HARD RULE forbids, or (b)
adding a test that passes against code no shipped binary executes, which
Stage 11 forbids.

This document is the "report it separately" that Stage 8 requires. It is
deliberately not a fix.

## 1. What the source branch actually does

Merge-base with this branch: `d1458a62` (2026-08-07). Three commits:

| Commit | Subject |
|---|---|
| `9628d70c` | fix(live): make the silent-host engine repair reachable |
| `35640219` | fix(live): enable the ADM output path so a silent host can broadcast |
| `06e641b1` | protect(live): lock the host output-path fix so it cannot silently regress |

The functional change is in `35640219`, and its first hunk is the decisive one:

```
 mobile-native/patches/@livekit+react-native-webrtc+144.1.1.patch | 27 +++-
```

The branch adds a native method — `audioDeviceModuleInitPlayout` — to the
**LiveKit** fork of `react-native-webrtc`, by patch. `initNativePlayout()` in
`liveAudioNative.ts` then calls it through `NativeModules.WebRTCModule`, and
`liveAudioEngine.ts` emits `audio_engine_playout_init_failed` when the returned
code is non-zero.

The telemetry event is not a description of an Agora failure. It is a
description of the return value of a method that exists only inside a LiveKit
patch. There is no Agora equivalent to point it at — `react-native-agora`
manages playout internally and exposes no init-playout return code.

The `mobile-native/package.json` delta on the branch is **not** a dependency
change; it only appends the new test path to `test:realtime-audio-critical` and
`test:realtime-audio`. Recorded here because a `package.json` line in an audio
diff usually means the media stack moved, and in this case it does not.

## 2. Why it cannot land: the repair path is orphaned on HEAD

The directive's Stage 3 asked for proof that `stabilizeLiveAudioEngine` and
`isStaleRecordingWithoutEngine` "are part of the current Live audio path," and
for their callers to be traced. They were traced. They are not part of it.

### 2.1 No production caller

```
$ grep -rn "stabilizeLiveAudioEngine" mobile-native/src --include='*.ts*'
mobile-native/src/live-audio/liveAudioEngine.ts:573:export async function stabilizeLiveAudioEngine(
mobile-native/src/live/__tests__/liveAudioDegrade.test.ts:1,71,92,128,164
```

Definition, two comment references, and one test file. Nothing else. The same
holds for the `core/` twin, `stabilizeRealtimeAudioEngine` — every reference
outside its own module is in `src/core/__tests__/`.

### 2.2 The whole family is a closed island

There are no barrel files (`src/core/index.ts`, `src/live-audio/index.ts` do not
exist), so an import graph is authoritative here. Outside their own directories,
these modules are imported by **tests only**:

| Module | Non-test importers |
|---|---|
| `core/realtimeAudioEngine.ts` | `core/realtimeMicrophonePublisher.ts`, `core/audioOwnershipPolicy.ts` (both themselves orphaned) |
| `core/realtimeMicrophonePublisher.ts` | none |
| `core/realtimeAudioTelemetry.ts` | only the modules above |
| `live-audio/liveAudioEngine.ts` | none |
| `live-audio/liveAudioNative.ts` | `live-audio/liveAudioEngine.ts` |
| `live-audio/liveMicrophonePublisher.ts` | `live/liveAudioPublisher.ts` |
| `live/liveAudioPublisher.ts` | none |
| `live/liveAudioTelemetry.ts` | `live/liveAudioTrace.ts` |
| `live/liveAudioTrace.ts` | none |
| `live/liveAudioRecovery.ts` | none |

Every arrow terminates inside the island. Nothing in `src/screens/`,
`src/components/`, or either engine owner enters it.

### 2.3 The two engine owners import none of it

`live/useAgoraLiveBroadcastRoom.ts` imports: `react-native-agora`,
`./liveSession`, `./useLiveBroadcastRoom`, `./agoraLiveTelemetry`,
`./liveSeatReconciliation`, `./liveParticipantRegistry`, `./liveAudioMatrix`,
`./liveStageLayout`, `./liveStreamQuality`, `./liveMusicMixing`.

`calls/callSessionStore.ts` imports: `react-native-agora`, `../api/calls`,
`../api/presenceSession`, `../core/mediaPlaybackCoordinator`,
`../core/voiceMessagePlayback`, `./callParticipants`, `./callSignalMedia`,
`./callKitBridge`, `./callToneLifecycle`, `./callSyncTrace`.

Neither reaches `live-audio/` or `core/realtimeAudio*`. The shipped Live audio
telemetry chokepoint is `live/agoraLiveTelemetry.ts`, not
`core/realtimeAudioTelemetry.ts`.

### 2.4 The bridge the repair reads has no implementation

`bridge()` in both `liveAudioNative.ts:86` and `realtimeAudioNative.ts:86`
resolves `NativeModules.WebRTCModule`. That module is provided by
`@livekit/react-native-webrtc`, which is not installed:

- `@livekit` — absent from `mobile-native/package.json`
- `livekit` — **0** matches in `mobile-native/package-lock.json`
- `mobile-native/patches/` contains exactly one file, `react-native+0.81.5.patch`
- `mobile-native/ios/Podfile.lock` lists `AgoraIrisRTC_iOS2 (4.6.2-build.1)` and
  `AgoraVideo_Special_iOS (4.6.2.70)`; there is no WebRTC or LiveKit pod
- `audioDeviceModuleGetEngineState` appears in four TypeScript files and in no
  `.m`, `.mm`, `.swift`, or `.h` file anywhere, including `node_modules`

So `readNativeAudioEngineState()` returns `null` in every build that can be
produced from this tree, `isStaleRecordingWithoutEngine(null)` is `false` by
design, and the bridge-based repair branch is unreachable at runtime.

`liveAudioEngine.ts` already knows this. It carries a `blindStaleRecorder`
fallback for exactly the no-bridge case:

```ts
const blindStaleRecorder =
  wantRecording && native === null && engineStopped && before.recordingRunning !== false;
```

That fallback is the part of the source branch's intent that **already exists**
on HEAD — see §4.

## 3. Why the source test cannot be ported as written

`liveHostEngineRepair.test.ts` installs its own bridge:

```ts
const modules = NativeModules as Record<string, unknown>;
function installBridge(state, initPlayout?) { ... modules.WebRTCModule = bridge; }
```

Its own docstring is candid about this: the failure it covers "only exists on a
binary that HAS it, because the bridge's reading is what the stale-recorder gate
consults."

On this tree no binary has it. A ported test would assign `WebRTCModule` into
`NativeModules`, exercise a branch no device reaches, and go green — which is
precisely the shape Stage 11 rules out: *"a test that can pass while production
never emits the event."* It would also read as coverage of the Live host path to
anyone scanning the suite list, which is worse than having no test, because a
missing test is visible and a misleading one is not.

The eight scenarios Stage 6 required, assessed against this tree:

| # | Scenario | Status here |
|---|---|---|
| A | Healthy engine, no repair | Covered — `liveAudioDegrade.test.ts`, `realtimeAudioEngine.test.ts` |
| B | Stale recording detected | Covered — `realtimeAudioNative.test.ts:100-151` (4 cases) |
| C | Repair required | Covered for the blind path; bridge path unreachable |
| D | Repair success | Covered for the blind path |
| E | Playout init failure telemetry | **Not portable** — requires the LiveKit ADM |
| F | Successful playout init | **Not portable** — same reason |
| G | Repeated repair / no duplicate emission | Covered by guard-generation tests; moot for E/F |
| H | Host media continuity | Not covered here and not coverable by this module — the host media path is `useAgoraLiveBroadcastRoom.ts`, which this family does not touch |

## 4. Disposition of the source branch content

| Item | Verdict | Reason |
|---|---|---|
| `audio_engine_playout_init_failed` telemetry | **REJECTED** | Reports the return code of a LiveKit-patched ADM method. No Agora analogue exists. Porting it requires reviving `@livekit/react-native-webrtc` — HARD RULE. |
| `initNativePlayout()` in `liveAudioNative.ts` | **REJECTED** | Same. Calls `bridge()?.audioDeviceModuleInitPlayout`. |
| `isStaleRecordingWithoutEngine` widening (dropping `&& !state.inputRunning`) | **REJECTED** | Behavioural change to detection, in a module with no production caller, justified by bridge readings this tree cannot obtain. Stage 8 forbids folding a behavioural change into a preservation task. |
| Host-repair *intent* — "a host whose engine stopped but whose recorder still claims to be live must be repaired, not declared healthy" | **SUPERSEDED** | `liveAudioEngine.ts`'s `blindStaleRecorder` already implements the no-bridge form of this on HEAD. |
| `liveHostEngineRepair.test.ts` | **REJECTED as written** | Mocks `NativeModules.WebRTCModule`. See §3. |
| `patches/@livekit+react-native-webrtc+144.1.1.patch` | **REJECTED** | LiveKit dependency. |
| Source branch declaration / baseline entries | **REJECTED** | They record physical validation of a LiveKit build. Copying them forward would put unearned "heard working" claims into this repo's rollback reference. |
| `package.json` suite-script additions | **REJECTED** | They only add the rejected test file to the two audio suites. |

## 5. Stage 14 — direct test coverage inventory

| Module | Direct test? | Indirect coverage? | Follow-up needed? |
|---|---|---|---|
| `core/realtimeAudioEngine.ts` | Yes — `realtimeAudioEngine.test.ts` | — | Yes: module has no production caller |
| `core/realtimeAudioNative.ts` | Yes — `realtimeAudioNative.test.ts` | — | Yes: bridge unimplemented |
| `core/realtimeAudioTelemetry.ts` | Yes | — | Yes: not the shipped Live telemetry path |
| `core/realtimeMicrophonePublisher.ts` | No | Via engine tests only | Yes: zero consumers |
| `core/audioOwnershipPolicy.ts` | Yes | — | Yes: only orphaned callers |
| `live-audio/liveAudioEngine.ts` | No direct file | `live/__tests__/liveAudioDegrade.test.ts` | Yes |
| `live-audio/liveAudioNative.ts` | No | Via `liveAudioDegrade` | Yes |
| `live-audio/liveMicrophonePublisher.ts` | No | Via `liveAudioPublisher.test.ts` | Yes |
| `live/liveAudioPublisher.ts` | Yes — `liveAudioPublisher.test.ts` | — | Yes: zero consumers |
| `live/liveAudioRecovery.ts` | Yes — `liveAudioRecovery.test.ts` | — | Yes: zero consumers |
| `live/liveAudioTelemetry.ts` | Yes | — | Yes: superseded by `agoraLiveTelemetry.ts` |
| `live/liveAudioTrace.ts` | Yes | — | Yes: only consumer is its own test |
| **`live/useAgoraLiveBroadcastRoom.ts`** | **No** | Partial — `liveSeatReconciliation`, `liveAudioMatrix`, `liveStageLayout`, `liveStreamQuality` tests cover extracted helpers | **Yes — this is the real Live audio owner and it has no direct test** |
| **`live/agoraLiveTelemetry.ts`** | **No** | `liveTelemetryPrivacy` tests cover the sanitiser | **Yes — and it is not in the protected-path manifest** |
| `calls/callSessionStore.ts` | Partial — `useAgoraCallRoom.test.ts` | — | Reviewed, adequate for now |

## 6. Follow-ups this raises — not actioned, deliberately

1. **Decide the fate of the orphaned family.** Twelve suites — **207 passing
   tests**, measured — exercise modules no build runs. That is more than half of
   the 369-test full audio battery. They are load-bearing for the *appearance*
   of audio coverage and for nothing else, and the 191-test "critical" suite
   inherits the same problem. Either wire them to Agora or delete them; leaving
   them is the option that keeps costing, because every future audio change pays
   for their upkeep and gets no protection back.

   This is not a recommendation to delete them today. The invariants they encode
   (one microphone owner, one publication, lease-based release) are the design
   the Agora owners are *supposed* to follow, and deleting the only written
   statement of that design before it is re-expressed against Agora would be a
   net loss.
2. **`live/agoraLiveTelemetry.ts` is unprotected while two dead telemetry
   modules are protected.** `config/realtime-audio-protected-paths.json`
   category `audio_telemetry` lists `core/realtimeAudioTelemetry.ts`,
   `live/liveAudioTelemetry.ts`, `live/liveAudioTrace.ts` — the shipped one is
   absent. Same for `liveSeatReconciliation.ts`, `liveStageLayout.ts`,
   `liveMusicMixing.ts`, `liveParticipantRegistry.ts`, all imported by the Live
   engine owner. This narrows but does not void the Item 2 claim: the two engine
   owners themselves *are* gated.
3. **`useAgoraLiveBroadcastRoom.ts` has no direct test.** It is the single Live
   engine owner, it is protected by path, and its behaviour is asserted only
   through extracted helpers.

Items 2 and 3 are Item 2 territory and belong in a separate change; Item 4's
Stage 21 restricts this commit to Item 4.

## 7. Gates run for this determination

No source file changed, so these establish the state the determination was made
against, not the effect of a change.

| Gate | Result |
|---|---|
| `npm run test:realtime-audio-critical` | 11 suites, **191 passed**, 0 failed, 0 skipped |
| `npm run test:realtime-audio` | 20 suites, **369 passed**, 0 failed, 0 skipped |
| `npm run test:realtime-audio-architecture` | 1 suite, **22 passed** |
| `python -m unittest tests.protection.test_realtime_audio_architecture` | **19 tests, OK** |
| `python -m unittest tests.protection.test_realtime_audio_gate_coverage` | **11 tests, OK** |
| `npx tsc --noEmit` | exit 0 |

Dependency audit: `@livekit` **ABSENT** (package.json, 0 lockfile matches, not in
`node_modules`); `react-native-webrtc` **ABSENT**; `patches/` contains only
`react-native+0.81.5.patch`; `ios/Podfile.lock` is Agora-only. No stale LiveKit
dependency was found, so no dependency cleanup was performed or needed.

New `createAgoraRtcEngine` call sites: **0**. New engine / microphone / camera /
`joinChannel` / audio-session owners: **0**. LiveKit code introduced: **0**.
LiveKit dependency introduced: **0**.

Four files still contain the string `livekit`. All four are tests, and none is a
dependency reference:

| File | Use |
|---|---|
| `core/__tests__/realtimeAudioArchitecture.test.ts:202` | Forbidden-term assertion: neither engine owner may match `/livekit\|registerGlobals\|livekitClient/i` |
| `live/__tests__/liveAudioTelemetry.test.ts:10,30,32` | Redaction fixture — a LiveKit-shaped URL and JWT used as sample secrets that must not survive `redact()` |
| `core/__tests__/realtimeAudioTelemetry.test.ts:48,55` | Same, for the core sanitiser |
| `live/__tests__/liveSeatReconciliation.test.ts:41` | Legacy provider value: asserts that a seat whose `provider` changes triggers `rejoin`. The string is test data standing in for "some other provider", not a code path |

The redaction fixtures are worth keeping as-is: a sanitiser proven against a
credential shape the product no longer mints is still proven against that shape,
and rewriting them to Agora URLs would delete coverage to make a grep tidier.
