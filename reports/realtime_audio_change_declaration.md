# Real-Time Audio Change Declaration

Change: PulseSoc stable livestream foundation and emergency audio recovery
Base: `c5e523d625166414573e618c1c043092794e7163`  
Baseline: `realtime-audio-stable-v1` (`fc25cd163b8802113df1b3b3d98cb7aab10891bb`)  
Required label: `audio-critical-change`

## Build 13 release-manifest addendum

This addendum records the protected manifest change in the release range
`02fee21c4e8c94c9e3f445fd24539a4c89f3ebba...5d3e5b7f3f965c73239e6be74b52cc2c36a236e3`.
The original push correctly failed the protection gate because the release
manifest changed without being named in this declaration. This corrective
record is intentionally explicit and does not reinterpret that failure as an
audio-runtime failure.

### Why the change is required

`mobile-native/app.json` was changed only to set the approved PulseSoc app icon
and the production iOS build number to `13` for the requested App Store build.
Those release fields are required for the production package, while the file is
also watched by the real-time audio guard because other manifest fields can
affect native dependencies and permissions.

### Which feature required it

PulseSoc production iOS build 13 packaging and approved app-icon delivery.

### Which protected files changed

| File | Category | Change |
|---|---|---|
| `mobile-native/app.json` | dependency watch | Updated release icon configuration and iOS build number only. No microphone permission, background-audio mode, native dependency, LiveKit setting, feature flag, or audio-session owner changed. |

### Expected behavior change

The production package uses the approved PulseSoc icon and reports build 13.
Call, video-call, and livestream audio behavior is unchanged.

### Regression risk

Audio-runtime risk is low because no audio setting, permission, dependency, or
implementation changed in the manifest diff. The broader release range still
ran the complete audio architecture, golden-flow, backend token, TypeScript,
native archive, simulator, and physical-device installation gates.

### Tests run

- Critical real-time audio suite: 16 suites / 317 tests passed.
- Full real-time audio suite: 21 suites / 387 tests passed.
- Native architecture gate: 1 suite / 22 tests passed.
- Backend architecture and LiveKit token/room policy: 22 tests passed.
- TypeScript compilation: passed.
- Signed production archive: `com.pulsesoc.app`, version `1.0.1`, build `13`, `** ARCHIVE SUCCEEDED **`.
- Exact archived app installed and launched on physical device `P3r7or`.

### Physical validation required

The manifest-only release metadata change does not alter the audio path, but
installing and launching a signed build is not audible proof. Paired host/viewer
livestream audibility and call/video-call regression remain release gates and
must not be claimed as newly performed by this addendum.

### Rollback procedure

Revert the build-13 release metadata commit and rebuild with the prior icon/build
configuration. No LiveKit, AVAudioSession, microphone publication, or backend
flag rollback is required for this manifest-only change.

## Why the change is required

Physical Live startup failed with `The native real-time audio engine did not remain active.` The stable rollback was already present, but the failed run lacked generation- and caller-complete evidence, and the release-blocking suite omitted the post-camera engine lifecycle test. The guard must remain fail-closed while the transition that invalidates the engine becomes observable and regression-tested.

A subsequent physical screenshot proved the first recovery was incomplete: it gated media-quality V2, while the exception is thrown only by the separate Live-audio V2 publisher path. Host/co-host publisher V2 must therefore require its own explicit server opt-in; the existing general audio V2 flag is no longer sufficient to move a publisher away from the stable path.

The permanent correction is an authoritative module-scoped Live runtime: stable session identity, explicit state transitions, event-derived readiness, idempotent commands, resource retention across UI remounts, and generation-scoped cleanup. This replaces screen navigation as an implicit lifecycle owner.

The remaining publisher divergence is removed by extracting the working video-call media order into `realtimePublisherMedia.ts`. Calls and Live now share one microphone-first coordinator: publish microphone, start camera, reassert microphone, then run the same post-camera native engine stabilization. Live retains its authorized role, portrait capture settings, and runtime state, but no longer owns a separate legacy/V2 AVAudioSession recovery sequence.

## Which feature required it

Emergency Live host/co-host audio recovery. No Marketplace, Advertising, Premium, Crypto, feed, or unrelated UI files are included.

## Which protected files changed

| File | Category | Change |
|---|---|---|
| `mobile-native/src/live/useLiveBroadcastRoom.ts` | livestream audio adapter | Emits ordered policy/owner/generation/camera/publication/verification events and stop-capable lifecycle events; snapshots flags and policy once per session. |
| `mobile-native/src/live/liveAudioTrace.ts` | audio telemetry | Adds required event names and privacy-safe correlation, generation, owner, room, screen, profile, flag, caller, reason, and timestamp fields. |
| `mobile-native/src/live/__tests__/liveAudioConfiguration.test.ts` | critical tests | Reproduces camera startup followed by native-engine loss and proves the guard fails closed. |
| `mobile-native/package.json` | dependency watch | Adds Live configuration and trace suites to the critical command; no dependency version changed. |
| `config/realtime-audio-protected-paths.json` | audio governance | Declares the required startup trace schema and critical lifecycle suite. |
| `tests/protection/test_realtime_audio_architecture.py` | critical tests | Enforces the trace contract and critical-suite inclusion. |
| `mobile-native/src/live/liveAudioFlags.ts` | audio feature flags | Adds a publisher-specific V2 gate that defaults off. |
| `mobile-native/src/live/liveSession.ts` | livestream audio adapter | Strictly normalizes the optional server publisher gate. |
| `mobile-native/src/live/__tests__/liveSession.test.ts` | critical tests | Locks the absent/malformed publisher gate to false. |
| `mobile-native/src/live/__tests__/cohostPublishGate.test.ts` | critical tests | Updates the exhaustive credential fixture for the new safe default. |
| `mobile-native/src/live/liveRuntime.ts` | authoritative Live runtime | Canonical session identity, state machine, typed errors, readiness, idempotency, resource registry, telemetry, and generation cleanup. |
| `mobile-native/src/live/__tests__/liveRuntime.test.ts` | critical tests | Valid/invalid transitions, readiness, duplicate start, stale cleanup, idempotent cleanup, and remount survival. |
| `mobile-native/src/screens/LiveHostSessionScreen.tsx` | livestream adapter consumer | Uses `startBroadcast`/`stopBroadcast` commands instead of transport-shaped calls. |
| `mobile-native/src/screens/LiveScreen.tsx` | livestream adapter consumer | Uses viewer join/leave commands. |
| `mobile-native/src/components/reels/ReelLiveViewerSurface.tsx` | livestream adapter consumer | Uses viewer join/leave commands. |
| `mobile-native/package.json` | dependency watch | Adds the Live runtime suite to critical and full audio commands; dependency versions unchanged. |
| `mobile-native/src/core/realtimePublisherMedia.ts` | shared audio-session coordinator | Defines the call-grade microphone/camera/reassert/stabilize sequence used by both video calls and Live. |
| `mobile-native/src/calls/useNativeCallRoom.ts` | call lifecycle adapter | Delegates its existing verified media ordering to the shared coordinator without changing call policy or room ownership. |
| `mobile-native/src/core/__tests__/realtimeAudioArchitecture.test.ts` | critical tests | Enforces that both adapters depend on the shared publisher coordinator. |
| `mobile-native/src/core/__tests__/mediaQualityWiring.test.ts` | critical tests | Moves the audio-before-camera ordering assertion to the shared coordinator that now owns that invariant. |

## Expected behavior change

Live publishers use the same call-grade media startup order and post-camera engine stabilization as working video calls. Server flags may still select the microphone publication implementation, but they no longer select a separate AVAudioSession recovery lifecycle. Audio ownership, routing, cleanup, Live authorization, portrait video quality, and runtime readiness remain governed by their existing owners.

## Regression risk

The shared coordinator changes Live startup and touches the call adapter to extract existing behavior. Architecture tests lock both consumers to the same coordinator, and focused call tests verify that audio-only calls never touch the camera. Residual risk is physical iOS camera/RemoteIO behavior, which automated tests cannot retire.

## Tests run

- Focused call/Live Jest: 2 suites / 22 tests passed.
- Critical audio Jest: 16 suites / 317 tests passed.
- Full audio Jest: 21 suites / 387 tests passed.
- Python architecture and token policy: 22 tests passed.
- TypeScript: passed.
- Change gate: rerun after this declaration.
- Native iOS Simulator Debug build: `** BUILD SUCCEEDED **`.
- Two-device audible QA: not run.

## Physical validation required

Install the corrected build on a physical iPhone; run two consecutive five-minute Live sessions without app restart; confirm a separate viewer hears the host both times; then confirm bidirectional audio call and video-call audio. Repeat stable and QA-only elite profiles. Automated or simulator results do not count as audible proof.

## Rollback procedure

Keep `REALTIME_MEDIA_QUALITY_V2_ENABLED=0` and `live_publisher_quality_enabled=false` server-side. If the shared call-grade branch must be rolled back, revert its focused commit; the previous Live-specific recovery paths remain in history. Confirm rollback on two physical devices before rollout.

---

# Addendum — post-camera recorder recovery is wired in (2026-08-05)

Change: repair the post-camera engine stabilization so it can actually restart
the recorder instead of failing the broadcast closed.
Required label: `audio-critical-change`

## Why the change is required

Physical Live startup still failed with `The native real-time audio engine did
not remain active.` after the shared call-grade coordinator landed. Tracing the
whole path shows why the previous fixes could not have worked at this call site.

`initializeCallGradePublisherMedia` calls the caller-supplied
`stabilizeAfterCamera` hook once, and lets it throw. In
`useLiveBroadcastRoom.connectTransaction` that hook was
`stabilizeRealtimeAudioEngine({ playout: true, recording: true, settleMs: 650 })`
with **no `reactivateSession`**. Device syslog recorded in the baseline shows the
camera transition leaves the shared AVAudioSession INACTIVE
(`cmsSetIsActive ... going inactive`) and iOS never delivers an
interruption-ended event while the camera holds the session. An ADM restart
issued against an inactive session silently no-ops. The guard therefore ran its
two passes against a session it could not start into, observed
`recordingRunning === false` both times, and threw — terminating a broadcast
whose microphone track had already been published successfully.

`recoverRealtimeRecordingEngine` was written for exactly this failure — it
re-activates the session with a plain `setActive(true)` before each ADM restart,
sweeps four passes across the asynchronous RemoteIO teardown window, and never
throws. It had **zero call sites**. It was dead code.

Separately, `stabilizeLivePublisherAudio` (which does pass `reactivateSession`)
is reached only from reconnect and camera-toggle handlers gated on
`publish && useV2`. `pulse_live_audio_v2_enabled()` in `bot.py` defaults OFF and
ships `LIVESTREAM_AUDIO_V2_ENABLED=false`. Production hosts therefore received
the throwing guard but none of the session-reactivating recovery.

## Which feature required it

Live host/co-host broadcast startup on physical iPhone. No unrelated subsystem
is touched.

## Which protected files changed

| File | Category | Change |
|---|---|---|
| `mobile-native/src/live/useLiveBroadcastRoom.ts` | livestream audio adapter | The `stabilizeAudio` hook now runs `recoverRealtimeRecordingEngine` (non-throwing, 4 passes, plain `startAudioSession` reactivation) before the authoritative guard, and supplies `reactivateSession` to the guard itself. Documents `useV2` as non-branching at `initializeLivePublisherMedia`. |

No other protected path changed. `realtimeAudioEngine.ts`,
`realtimePublisherMedia.ts`, the ownership policy, the microphone publisher, and
the protected-paths manifest are untouched.

## Expected behavior change

A camera-induced recorder teardown is now recovered instead of being reported as
a fatal broadcast failure. The fail-closed invariant is preserved: the
authoritative `stabilizeRealtimeAudioEngine` still runs last and still throws if
the engine is genuinely dead, so a silent broadcast can never be reported as
healthy. The only broadcasts that change outcome are those that previously
aborted while the engine was recoverable.

Startup ordering is unchanged. No AVAudioSession category is reasserted at this
site — reactivation is a plain `setActive(true)`, deliberately not a category
reassert, which the baseline recorded as disruptive to the running WebRTC video
pipeline. No second microphone track, no second publication path, no new global
audio singleton, no screen-level session setup, no ownership bypass.

## Regression risk

The added recovery is best-effort and swallows its own errors, so it cannot
introduce a new failure mode of its own. The residual risk is timing: recovery
adds up to ~1.6s of settle passes to a startup that is already failing, and only
on the path where the engine is observed stopped. Healthy startups skip every
pass on the first inspection. Physical iOS camera/RemoteIO behavior remains
untestable in CI.

## Tests run

Re-run in full on 2026-08-05. The earlier entry recorded the Python protection
suite as unrunnable here; that is no longer true, and the suite itself has since
been rebuilt (see below), so the numbers are restated rather than appended.

- TypeScript (`tsc --noEmit`): passed, no diagnostics.
- Jest, entire native app: **160 suites / 2,820 tests passed** (run as six
  shards; no failures in any shard).
- Jest, audio-critical set only (the 13 paths named by
  `test:realtime-audio-critical`): **181 tests passed**.
- i18n catalogue validation: OK, 11 locales. Three advisory `many`-plural
  warnings for es/fr/pt are pre-existing and unrelated.
- Python protection suite: **200 checks across 12 suites, passed**
  (`scripts/protection/run_protection_suite.py`).

  This line is worth reading carefully rather than as a green tick. Before this
  mission the runner named three files explicitly and the CI job invoked
  `python3 -m unittest` against modules that define plain functions and no
  `TestCase` — so it collected nothing, printed "Ran 0 tests ... OK", and exited
  zero. The job guarding LiveKit publish grants, which is the authorization
  deciding who may turn on a microphone, was green while measuring nothing. The
  runner now discovers every suite and fails any suite that exits zero having
  executed zero checks. The 200 is the first count from this file that could
  have been a smaller number.

- Change gate (`scripts/realtime_audio_change_gate.py --base origin/main
  --head HEAD`): reports no protected path changed, because the working-tree
  edit to `useLiveBroadcastRoom.ts` is not yet committed and the gate diffs
  commits. `useLiveBroadcastRoom` **is** in the protected manifest, so the gate
  will fire on the pushed range and this declaration is what it will look for.
- Native iOS `Info.plist` invariants, read from the checked-in project
  (`ios/PulseSocNative/Info.plist`): `NSMicrophoneUsageDescription` present,
  `UIBackgroundModes` contains `audio`. `app.json` declares both, so prebuild
  reproduces them.
- Native iOS build (`expo prebuild` on macOS) and two-device audible QA:
  **NOT RUN.** See below.

## Physical validation required

Not yet performed and explicitly not claimed. No simulator was used either, and
a simulator result would not change this section: the simulator does not
reproduce AVAudioSession hardware arbitration, RemoteIO teardown on camera
transition, or route changes, which are the exact mechanics this change exists
to survive.

The full procedure is `docs/realtime_audio_live_test_matrix.md`. The minimum bar
before shipping: install on a physical iPhone; start a Live broadcast and
confirm a separate viewer hears the host; toggle the camera mid-broadcast and
confirm audio survives; switch front/rear camera and confirm audio survives; run
two consecutive five-minute sessions without an app restart; then confirm a
bidirectional audio call and a video call still have audio.

## Rollback procedure

Revert this single commit. It touches one file and adds one call; the previous
behavior (single guard, no reactivation) is restored exactly. No server flag,
no schema, and no native dependency is involved, so no coordinated rollback is
required.
