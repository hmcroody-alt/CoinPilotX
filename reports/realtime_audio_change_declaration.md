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

---

# Addendum — the guard was requiring a signal a healthy host cannot produce (2026-08-05)

Change: separate "try to start playout" from "fail the broadcast if playout is
down", and stop requiring playout at the two Live publisher call sites.
Required label: `audio-critical-change`

## Why the change is required

This is a correction to the commit above, and it is worth stating plainly: the
previous addendum shipped a guard that killed healthy broadcasts. The recovery
work was right; the pass/fail condition attached to it was not.

Commit `5f76e30` was installed on physical iPhone `P3r7or` as a Release build.
Every Live broadcast attempt failed on screen with *"Broadcast audio could not
stay active while the camera started."* Console.app on the device, filtered to
`PulseSocRealtimeAudio`, captured three complete and identical attempts:

| correlationId | recover started | recover done | guard started | guard failed |
|---|---|---|---|---|
| `rt-msgrl1bm-3` | 17:15:11.588 | 17:15:12.848 | 17:15:12.849 | 17:15:13.513 |
| `rt-msgrlqod-4` | 17:15:40.564 | 17:15:41.823 | 17:15:41.824 | 17:15:42.485 |
| `rt-msgropfb-5` | 17:18:02.591 | 17:18:03.844 | 17:18:03.844 | 17:18:04.510 |

All three reported, verbatim:

```
outcome: 'engine=true;playout=false;recording=true'
failureCategory: 'native_engine_not_running'
```

Read that against the original incident. The defect this whole effort exists to
fix had `recording=false` after camera startup — the microphone was genuinely
gone. Here `recording=true` and `engine=true`. **The camera-teardown defect is
fixed.** What killed these three broadcasts was the guard's own requirement that
`playout` also be running.

A Live host at startup publishes into a room with no remote participants. There
is no remote audio to render, so AURemoteIO's output side is not running and
`startPlayout()` is a no-op with no sink. The timings corroborate it: the
recover stage burned its full four-pass sweep (~1.26s) without an early break
and the guard ran a further 0.66s — roughly six restart attempts across ~1.9s,
every one of which brought recording up and none of which could bring playout
up. Requiring playout there is a red light no healthy host can ever turn green,
which is the inverse of the failure mode this mission was chartered against.

## Which feature required it

Live host/co-host broadcast startup on physical iPhone. Calls are deliberately
left alone.

## Which protected files changed

| File | Category | Change |
|---|---|---|
| `mobile-native/src/core/realtimeAudioEngine.ts` | canonical audio engine | Adds an optional `requirePlayout` to `stabilizeRealtimeAudioEngine`, defaulting to `options.playout` so every pre-existing call site keeps identical semantics. Playout is still started; only the failure condition is separable. The telemetry `outcome` now carries `required=playout:…,recording:…`. |
| `mobile-native/src/live/useLiveBroadcastRoom.ts` | livestream audio adapter | Passes `requirePlayout: false` at the two publisher sites — `stabilizeLivePublisherAudio` and the `stabilizeAudio` hook inside `connectTransaction`. `recording` stays required at both. |
| `mobile-native/src/core/__tests__/realtimeAudioEngine.test.ts` | critical tests | Three regression tests, each mutation-verified (see below). |

### Protected files changed earlier in this range, declared here

The gate diffs the whole range against `origin/main`, so these files from the
protection-suite repair earlier in the mission are named here too. None of them
alters audio runtime behaviour; all four are the measuring apparatus.

| File | Category | Change |
|---|---|---|
| `.github/workflows/realtime-audio.yml` | audio governance | Invokes the discovering protection runner instead of `python3 -m unittest` against modules that define no `TestCase`. The old invocation collected nothing, printed "Ran 0 tests ... OK", and exited zero — a green job measuring nothing. |
| `tests/protection/test_livekit_webhook_route_owner.py` | critical tests | Converted to checks the runner actually executes; asserts the webhook route owner is unchanged. |
| `tests/protection/test_livestream_audio_token_grants.py` | critical tests | Same conversion; asserts LiveKit publish grants — the authorization deciding who may turn on a microphone. |
| `tests/protection/test_livestream_contract.py` | critical tests | Same conversion; asserts the livestream API contract. |

`stabilizeLiveViewerAudio` is **not** changed. A viewer with playout down
genuinely hears nothing, so it keeps `playout: true, recording: false` and stays
fail-closed on playout. All five call sites in `useNativeCallRoom.ts` are
untouched and remain fail-closed on playout, because a caller who cannot hear
the other party has a broken call.

## The fail-closed invariant is preserved

`recording` remains required for publishers. A genuinely silent broadcast — the
microphone denied at B1, or revoked mid-broadcast at B2 — still throws
`REALTIME_AUDIO_ENGINE_INACTIVE`. The change narrows what the guard is willing
to fail on; it does not make the guard optional. The `required=` field added to
the telemetry outcome exists so that a single log line distinguishes "playout
was down and that was fine" from "we shipped a silent broadcast anyway" — the
absence of that distinction is what made this defect take a device to find.

## Tests run

- `realtimeAudioEngine.test.ts`: 23 tests passed.
- Audio-critical set (17 suites): **228 tests passed**.
- TypeScript (`tsc --noEmit`): passed, no diagnostics.
- i18n catalogue validation: OK, 11 locales.

Each new test was verified to be capable of failing, by mutating the source and
confirming the specific test goes red:

| Mutation to `realtimeAudioEngine.ts:395` | Test that went red |
|---|---|
| `const requirePlayout = options.playout` (ignore the option — the shipped bug) | *does not fail a Live host whose playout is down* |
| `const requirePlayout = options.requirePlayout ?? false` (never require playout) | *keeps calls fail-closed on playout by default* |

The source was restored and all 23 tests re-run green after each mutation. A
test that cannot go red is not a test, and these were not trusted until they
had.

## Physical validation required

The three Console captures above are physical-device evidence of the *failure*
and of the camera-teardown fix. They are not yet evidence that a viewer hears
the host — that still requires a second physical iPhone and
`docs/realtime_audio_live_test_matrix.md` Group A and Group B. Rows B1 and B2
matter more than usual for this change, because they are what prove the guard
was narrowed rather than disabled.

## Rollback procedure

Revert this commit. `requirePlayout` defaults to `options.playout`, so removing
the option restores the previous condition exactly. No server flag, schema, or
native dependency is involved.

---

# Addendum — telemetry carries the native diagnostic, and two build files moved (2026-08-06)

## Why the change is required

The previous addendum closed the guard defect but left the instrument that
found it half-blind. Two concrete gaps:

1. **The native reading never survived the log line that reported it.** The
   diagnostic explaining *why* `engine=false` was concatenated onto `outcome`,
   which `sanitize()` clipped at 96 characters. The field that mattered was
   truncated away by the very function meant to make it safe to log.
2. **A bounded recovery pass was indistinguishable from a second guard entry.**
   Recovery re-emitted `audio_engine_guard_started` with an identical context,
   so a single guard doing its job looked, in a device capture, exactly like the
   guard being entered twice.

Both are diagnosis defects, not runtime defects. Neither changes when audio
starts, stops, or who owns the session.

## Which feature required it

Device-log diagnosis of Live host broadcast startup. No user-facing feature.

## Which protected files changed

| File | Category | Change |
|---|---|---|
| `mobile-native/src/core/realtimeAudioTelemetry.ts` | audio telemetry | Adds `audio_engine_recovery_attempt` as a distinct event name; adds the `engineState`, `nativeError`, `recoveryAttempt`, `failureStage` and `interruption` fields; gives `sanitize()` a per-call `maxLength` (default unchanged at 96) so native diagnostics can be carried at 480. Adds an opt-in `setRealtimeAudioTelemetryVerbose()` and demotes non-failure events from `console.error` to `console.log` by default. |
| `mobile-native/src/core/__tests__/realtimeAudioTelemetry.test.ts` | critical tests | Covers the new fields, the widened cap, the severity split, and that redaction still precedes truncation. |
| `mobile-native/eas.json` | build configuration | Adds `EXPO_PUBLIC_STORE_READINESS: "1"` to the `development`, `development-simulator` and `preview` profiles. Nothing else in the file changed. |
| `mobile-native/package-lock.json` | dependency lock | Adds the `@types/react-test-renderer` devDependency and its single transitive entry. Nothing else in the file changed. |

**Explicitly not changed by this addendum:** no microphone permission string, no
`UIBackgroundModes` entry, no LiveKit room or track setting, no
`AVAudioSession` category/mode/option, no audio-session ownership arbitration,
no `expo-av` call site. `eas.json` gained one public env var on three non-store
profiles; the `production` profile is untouched. `package-lock.json` gained a
types package that does not exist at runtime.

## Expected behavior change

None audible. Audio start-up, recovery bounds, ownership and the fail-closed
conditions are byte-for-byte the semantics of the previous addendum. What
changes is the shape of a log line: failures still log at error level, healthy
transitions now log at `console.log` unless verbose is switched on for a
capture session.

## Regression risk

The widened `sanitize` cap is the only place a security question arises, and it
is ordered against it: redaction runs over the full string *before* the slice,
so a longer cap cannot expose anything a shorter one hid — it can only let an
already-redacted diagnostic survive intact. The default cap is unchanged at 96,
so every pre-existing field keeps identical output.

The severity demotion could in principle hide a transition from a Release-build
device capture, since iOS `os_log` drops non-error levels in Release. That is
why `setRealtimeAudioTelemetryVerbose(true)` exists and why it defaults to off:
capture sessions opt in, and failure events never depend on it.

## Tests run

- `realtimeAudioTelemetry.test.ts`, `realtimeAudioEngine.test.ts`,
  `liveAudioConfiguration.test.ts`: **76 tests passed**.
- Full native suite via `npm run verify` (typecheck + i18n + jest): **170
  suites, 3004 tests passed**, 11 locales OK, `tsc --noEmit` clean.
- Backend protection suite: 15 suites, 239 checks passed.

## Physical validation required

Unchanged from the previous addendum — `docs/realtime_audio_live_test_matrix.md`
Groups A and B still require two physical iPhones and are not satisfied by any
of this. This addendum adds no new physical requirement of its own, because it
adds no new runtime behaviour; the value it delivers is only visible *during*
that physical validation, in the log capture.

## Rollback procedure

Revert this commit. The new telemetry fields are optional and additive, the
`sanitize` cap defaults to its historic value, `verbose` defaults to off, the
`eas.json` env var is read only by store-readiness gating on non-production
profiles, and the lock entry is a types-only devDependency. No server flag,
schema, or native dependency is involved.

## Unified live audio path addendum (2026-08-06)

This addendum records a deliberate real-time audio mission: unify every live
session onto the single physically verified call-grade audio path and remove
the legacy livestream audio branch's behavioural divergence.

### Why the change is required

Production Railway holds `LIVESTREAM_AUDIO_V2_QA_ONLY=true` with a two-user QA
allowlist and `LIVESTREAM_AUDIO_V2_PERCENT=0`, so every real host and viewer
ran the legacy path. The legacy path retained the duplicate-publication publish
defect (150ms poll + mic toggle) and had NO automatic reconnect, NO token
refresh (30-minute guest tokens silently expired), NO output-route reapply
(iOS moving output to the receiver was how Live went quiet with no error), NO
foreground/interruption recovery, and NO publisher reconnect stabilization.
The audience-silence complaint is this divergence.

### Which feature required it

Livestream host/viewer audio (audience must hear the host) and voice-message
send latency.

### Which protected files changed

| File | Change |
|---|---|
| `mobile-native/src/live/useLiveBroadcastRoom.ts` | Every live session now runs the unified event-verified publisher (`publishLiveMicrophone`); the legacy `ensureMicrophonePublished` survives only as a one-shot rescue when the verified publish settles with zero tracks. Removed the `useV2` gates from `reapplyAudioRoute`, `scheduleTokenRefresh`, `scheduleReconnect`, the route-change/AppState foreground listeners, the Reconnected/TrackSubscribed publisher stabilization, mid-session camera stabilization, and disconnect-classification telemetry. `useV2` is retained strictly as a telemetry cohort label. |
| `mobile-native/src/live/liveAudioFlags.ts` | Comments only: the server flag is documented as a telemetry cohort label. Parsing behaviour unchanged (strict `=== true`). |
| `mobile-native/src/screens/ChatScreen.tsx` | Voice recording switched from the stereo `HIGH_QUALITY` preset to voice-tuned mono AAC 24 kHz / 32 kbps (4-8x smaller upload). expo-av allowlist file count unchanged. |
| `mobile-native/src/api/messenger.ts` | Voice uploads skip the awaited `/api/messages/media/complete` round trip, which is a functional no-op for voice (`/upload` already persists duration/waveform, sets processing_status, and enqueues processing jobs). Non-voice media is unchanged. |

### Expected behavior change

Livestream hosts and viewers get the same publish verification, duplicate
reconciliation, route reapply, token refresh, bounded reconnect, and
foreground recovery that calls use. Audio-call and video-call code paths are
untouched. Voice messages send roughly one RTT sooner with a much smaller
payload.

### Regression risk

Low-to-moderate. The unified publisher and recovery machinery are the code the
2026-08-02 baseline (`realtime-audio-stable-v1`) physically verified as
audible for QA sessions; this change extends them to all sessions rather than
introducing new mechanisms. The legacy publish mechanism remains available as
an explicit rescue, and the fail-closed `LIVE_LOCAL_AUDIO_NOT_PUBLISHED` check
still guards every publisher connect. No AVAudioSession ownership, LiveKit
publication path, or engine arbitration changed.

### Tests run

- `npm run test:realtime-audio-critical`: 16 suites, 349 tests passed.
- `npm run test:realtime-audio`: 22 suites, 444 tests passed.
- `npm run test:realtime-audio-architecture`: 1 suite, 22 tests passed.
- Backend architecture (`tests.protection.test_realtime_audio_architecture`): 18 tests OK.
- Backend token grants + webhook route owner: 4 tests OK.
- `tsc --noEmit`: clean. `npm run i18n:validate`: 11 locales OK.
- live/core/calls/api/screens jest: 135 + 494 + 866 + 454 tests passed.

### Physical validation required

`docs/realtime_audio_live_test_matrix.md` Groups A and B on two physical
iPhones: host goes live, audience hears audio; join/leave does not break
audio; audio call and video call baseline re-checks; voice message send
latency observation. Railway note for shipped builds: existing binaries still
branch on the server flag, so set `LIVESTREAM_AUDIO_V2_QA_ONLY=0` and
`LIVESTREAM_AUDIO_V2_PERCENT=100` (keep `LIVESTREAM_AUDIO_V2_ENABLED` as kill
switch) until this build is fully rolled out.

### Rollback procedure

Revert this commit. No server flag semantics, schema, or native dependency
changed; the client returns to branching on the server flag exactly as before.
