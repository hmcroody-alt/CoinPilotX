# Real-Time Audio Change Declaration

Change: PulseSoc stable livestream foundation and emergency audio recovery
Base: `c5e523d625166414573e618c1c043092794e7163`  
Baseline: `realtime-audio-stable-v1` (`fc25cd163b8802113df1b3b3d98cb7aab10891bb`)  
Required label: `audio-critical-change`

## App-review call-lifecycle addendum (2026-08-19)

This addendum declares the protected-file changes in branch
`codex/app-review-final-readiness` (App Review readiness item 2: a call must
survive in-app navigation, and one party hanging up must end the call for both).

### Why the change is required

App Review item 2 failed audit: the Agora call engine was owned by the
`useAgoraCallRoom` hook mounted inside `CallScreen`, so navigating away from the
screen unmounted the hook and tore down the live call. Ownership had to move to
a module-scoped store so the engine, status polling, cues, CallKit reporting,
and wake lease survive navigation and are released only on explicit hangup, a
terminal backend status, or a 404/410 poll miss.

### Which feature required it

App Review readiness item 2 (calls survive in-app navigation; remote hangup
terminates promptly for both parties). No audio-quality, AVAudioSession,
microphone-publication, or livestream change was made or authorized.

### Which protected files changed

| File | Category | Change |
|---|---|---|
| `mobile-native/src/screens/CallScreen.tsx` | protected call UI | Became a thin consumer of the new module-scoped `src/calls/callSessionStore.ts`. No AVAudioSession calls, no new audio track or publication path, no audio-mode changes. "Minimize" now only navigates; it does not release the engine. |
| `mobile-native/src/calls/__tests__/useAgoraCallRoom.test.ts` | protected call tests | Updated to test the hook as a thin binding over the store; assertions that unmount tears down the engine were replaced with assertions that it does not. |

Supporting non-protected files: `src/calls/callSessionStore.ts` (new, single
owner of the engine lifecycle), `src/calls/useAgoraCallRoom.ts` (now a 38-line
binding), `src/calls/MinimizedCallBanner.tsx` (new, return-to-call banner),
`src/navigation/AppNavigator.tsx` (mounts the banner),
`src/calls/__tests__/callSessionStore.test.ts` (new store tests). The
one-audio-singleton rule is preserved: exactly one engine owner exists
(the store), and the screen no longer creates or destroys it.

### Expected behavior change

Navigating away from `CallScreen` keeps the call alive and shows a minimized
banner; returning re-binds the same session. `onUserOffline` and
connection-failure events trigger an immediate deduped status re-fetch, so a
remote hangup terminates the call for the local party promptly instead of
waiting for the next poll tick. Audio capture, routing, session category, and
livestream behavior are unchanged.

### Regression risk

Moderate and confined to call lifecycle: the risks are a leaked engine after
hangup or a stale session on re-entry. Mitigated by release paths on explicit
hangup, terminal statuses, and 404/410 poll misses, plus dedicated store tests.
Livestream code paths are untouched (zero livestream/LiveKit lines in the diff).

### Tests run

- Call jest suites: 6 suites / 43 tests passed, including the new
  `callSessionStore.test.ts` (5 tests) and the rewritten
  `useAgoraCallRoom.test.ts`.
- TypeScript compilation (`npx tsc --noEmit`): passed.
- i18n catalog validation: passed; no new hardcoded strings introduced.
- The full realtime-audio suites and a native build were NOT run in this
  environment; they remain required release gates before merge (see below).

### Physical validation required

Two-device physical test before release: place a call, navigate away and back
on each side (call must stay audible both ways), then hang up from one side and
confirm the other side terminates within a poll cycle. CallKit lock-screen
controls must still work. This declaration does not claim audible validation
was performed.

### Rollback procedure

Revert the item-2 commit on `codex/app-review-final-readiness` (files listed
above). No AVAudioSession, permission, native-dependency, backend-flag, or
livestream rollback is required.

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

## Viewer room_connected guard made non-fatal (physical-test fix)

### Why the change is required

Physical two-device QA of the unified path: host broadcasts with video fine,
audience hears nothing, no visible error. Root cause: at `room_connected` the
viewer has subscribed no remote audio yet, so the AUDIENCE health guard
(fail-closed on playout) can throw against a healthy connection. That await in
`connect()` had no try/catch, so the throw failed connect and
`ReelLiveViewerSurface` silently fell back to HLS — Mux video with no audio.

### Which protected files changed

| File | Category | Change |
|---|---|---|
| `mobile-native/src/live/useLiveBroadcastRoom.ts` | audio runtime | Viewer-side `stabilizeLiveRemotePlayback` at stage `room_connected` wrapped in try/catch; failure now emits `viewer_room_connected_stabilize_deferred` (trace + `PulseSocLiveAudio` console.error) and the LiveKit connection proceeds. |
| `mobile-native/src/live/liveAudioTrace.ts` | audio telemetry | Added trace event name `viewer_room_connected_stabilize_deferred`. |

### Expected behavior change

Viewers stay on the LiveKit path when the pre-track playout probe fails; host
audio becomes audible when tracks arrive. The authoritative AUDIENCE playout
check still runs at `track_subscribed` (already fire-and-forget), and every
publisher-side fail-closed guard is unchanged — a silent BROADCAST still fails
loudly. Only the viewer's pre-subscription probe stopped being fatal.

### Tests run

- live jest suites: 11 suites, 135 tests passed.
- live + core jest: 31 suites, 584 tests passed.
- `tsc --noEmit`: clean.

### Rollback procedure

Revert this commit; viewer connect returns to failing closed at
`room_connected` (with HLS fallback behavior).

---

# Addendum: dedicated Live audio copy (`src/live-audio/`)

Change: duplicate the working call audio control flow into a Live-only module set,
move Livestream onto the copy, and repair the two Live-specific faults the copy
made addressable.
Base: `3757dbfb` (branch `codex/emergency-live-audio-recovery`)
Required label: `audio-critical-change`

## Why the change is required

The owner directed that Livestream stop depending on the call implementation, so
that Live-specific stabilization, flags, recording preparation and ownership logic
cannot interfere with calls. Before this change both surfaces resolved to the same
`src/core/` modules, so any Live fix was a call risk and any call fix was a Live
risk. That coupling is what this addendum removes.

The change is not only structural. Two faults found during the preceding audit are
fixed here, and both are Live-only:

**1. The audience health profile had a required check with no repair behind it.**
`AUDIENCE` is `{playout: true, recording: false, requirePlayout: true}`. Inside the
guard's `enforce()`, every ADM restart - the stale-recorder branch and the ordinary
restart branch - is gated on `wantRecording`. That gating is correct for a call,
where the local participant always records, so `startLocalRecording()` (the SDK's
init-and-start operation) is always reachable. A Live audience must never record,
because a second capture path would steal the session. The consequence is that a
viewer was excluded from every repair the guard owns: the only operation it could
reach was `startPlayout()`, which resumes an already-initialized engine and is a
no-op against an uninitialized one. `engineRunning` and `playoutRunning` stayed
false, and the guard then failed on `requirePlayout` - a condition it had no branch
capable of fixing. The viewer was asked to prove playout was up and given no way to
bring it up.

The repair added to the copy is the same shape as the existing stale-recorder
branch, applied to the output side, and is deliberately narrow: it runs only when
recording is *not* wanted, so it can never fire on a host or on a call participant,
and only after the ordinary `startPlayout()` has already been tried and left the
engine down. It declares engine availability (input false, output true) so the ADM
is not asked to start an output it believes does not exist, then does an explicit
`stopPlayout()` followed by `startPlayout()` - the stop being what clears a playout
path left ENABLED over a dead engine, the state in which the next start
short-circuits on an ADM that already answers "playing".

**2. The Live host's post-camera microphone reassert was wired to the publisher.**
`initializeLivePublisherMedia` passed `reassertMicrophone: options.publishMicrophone`.
The publisher returns `already_published` as soon as any audio publication exists,
so it never reached the `setMicrophoneEnabled(true)` and per-publication
`track.setEnabled(true)` that the reassert step exists to perform. Camera start is
precisely the transition that can leave the native media track disabled, so the one
moment the repair was needed was the one moment it did nothing. The call path has
always passed the real `reassertRealtimeMicrophone` and calls have working audio.
The Live call site now passes the real one too.

## Which feature required it

PulseSoc Livestream audio: audience audibility of host and co-host.

## Which protected files changed

| File | Category | Change |
|---|---|---|
| `mobile-native/src/live-audio/liveAudioEngine.ts` | NEW - livestream_audio_adapter | Copy of `core/realtimeAudioEngine.ts`, renamed. Adds the receive-only playout repair described above. |
| `mobile-native/src/live-audio/liveMicrophonePublisher.ts` | NEW - microphone_track_and_publication_controller | Copy of `core/realtimeMicrophonePublisher.ts`, renamed. No behavioral change. |
| `mobile-native/src/live-audio/liveAudioNative.ts` | NEW - runtime_invariant_monitor | Copy of `core/realtimeAudioNative.ts`, renamed. No behavioral change. |
| `mobile-native/src/live-audio/livePublisherMedia.ts` | NEW - authoritative_live_runtime | Copy of `core/realtimePublisherMedia.ts`, renamed. No behavioral change; pinned identical by test. |
| `mobile-native/src/live/useLiveBroadcastRoom.ts` | livestream_audio_adapter | Audio imports repointed from `../core/` to `../live-audio/`. Passes the real reassert. No other logic touched. |
| `mobile-native/src/live/liveAudioPublisher.ts` | livestream_audio_adapter | Re-export target moved to the copy. Remains a re-export, not a second implementation. |
| `config/realtime-audio-protected-paths.json` | audio_governance | Copy files added to `allowed_paths` and `import_boundary`. |
| `mobile-native/src/core/__tests__/realtimeAudioArchitecture.test.ts` | critical_audio_tests | The "shared publisher" assertion is replaced, not removed. See below. |

**No file under `mobile-native/src/core/` or `mobile-native/src/calls/` changed.**
The call path is byte-identical to `3757dbfb`.

## What was deliberately NOT copied

`core/audioOwnershipPolicy.ts`. It is the single registry deciding who currently
holds the microphone. A second copy would give Live a registry that knows nothing
about an in-progress call, and Live would take a session a call still owned. Copying
it would break calls, which outranks the symmetry of copying everything. Live imports
the shared one. `realtimeAudioTelemetry` and `realtimeAudioInvariants` are also
shared: both are reporting-only and neither can steal a session.

## Governance change: what the manifest now permits

The forbidden-API rules are not relaxed. Each affected rule now names two owners
instead of one - the call module and its Live copy - and every other file in the
repository is still refused. `expo_av_global_audio_mode` is untouched and remains
frozen at six paths.

The architecture test previously asserted "keeps calls and Live on the shared
call-grade publisher sequence". That rule is now false by design, so it was
rewritten rather than deleted. The old rule protected sameness *by sharing*. The
replacement protects sameness *by copy*, which is weaker and therefore needs two
assertions: each adapter uses its own module and cannot reach the other's, AND the
two publisher modules are still step-for-step identical once comments and the
Realtime/Live naming are folded out. Without that second assertion the copy would be
free to drift, and the drift would stay invisible until a broadcast went silent.

## Expected behavior change

Livestream audience playout gains a repair path it did not have. Live host gains the
post-camera microphone reassert that calls already had. Audio-call and video-call
behavior is unchanged in every respect.

## Regression risk

The main risk is drift between the copy and the original, accepted knowingly as the
cost of the isolation the owner asked for, and mitigated by the identity test above.
The receive-only repair calls `stopPlayout`/`startPlayout`/`setEngineAvailability`,
which are guarded by `typeof`/optional-call so an SDK build lacking them degrades to
present behavior rather than throwing.

## Validation status

Passing: `npm run typecheck`; `npm run test:realtime-audio-critical` (16 suites,
350 tests, including `callAudioOwnershipRegression` and `useNativeCallRoomAudio`);
`npm run test:realtime-audio-architecture` (23 tests).

**NOT performed: physical audible validation on two devices.** No device or
simulator was available in this environment. Section 7 of
`reports/realtime_audio_verified_baseline.md` is therefore NOT satisfied by this
change, and this addendum must not be read as claiming it is. Live audio remains
unproven on hardware until PHONE A / PHONE B is run against a build cut from this
commit. The build number was moved to 14 so such a build can exist; build 13 predates
`c9147482` and cannot demonstrate any of this.

---

# Addendum 2 — the host could not go live at all

## What the device showed

A host on a build newer than 13 got a full-screen "Broadcast could not start /
Broadcast audio could not stay active while the camera started." That sentence is
`describeLiveAudioFailure("camera_start")`. The guard threw
`LIVE_AUDIO_ENGINE_INACTIVE` out of `connect()` and ended a broadcast whose
microphone track was already published.

Addendum 1 reasoned about a *silent viewer*. That was incomplete: the more severe
fault was a host who never got on air.

## Fault 1 — the repair was unreachable without the native bridge

The wedge state is engine dead + ADM answering `isRecording === true` (always-prepared
keeps the record path ENABLED across an engine stop). In that state:

- `staleRecorder` needs `readNativeAudioEngineState()`, which returns `null` on any
  binary built without `patches/@livekit+react-native-webrtc+144.1.1.patch`.
  `isStaleRecordingWithoutEngine(null)` is `false` by design — with no native reading
  there is no honest way to call a recorder stale.
- the `else if` branch needs `recordingRunning === false`; this state reports `true`.
- `startPlayout()` no-ops on an uninitialised ADM.

So the guard declined every repair, then threw on the untouched state.

`liveAudioEngine.ts` gains `blindStaleRecorder`: same repair, reachable when
`native === null`, gated on `engineStopped`. If the engine is not running there is no
capture in flight, so the stop cannot tear down a live recorder — the exact hazard the
bridge was introduced to prevent. When the bridge is present its reading still wins.

## Fault 2 — an unconfirmed engine ended the broadcast

Three `stage: "camera_start"` call sites in `useLiveBroadcastRoom.ts` threw: the
connect path, `setCameraEnabled`, and `switchCamera`. All three now degrade via
`confirmLiveAudioOrWarn`, which converts **only** `LIVE_AUDIO_ENGINE_INACTIVE` into a
new `audioWarning` string. Every other error still propagates and still ends the
broadcast.

This is the shape the viewer path already used at `room_connected`: the early check is
advisory, a later pass is authoritative. The connect path schedules one 1500 ms
re-check; `recheckAudio()` gives the host a manual one.

**The invariant is not weakened.** A silent broadcast is still never reported as
healthy — `LiveHostSessionScreen` renders a warning banner with a Retry, and
`diagnosticCode` is set to `LIVE_AUDIO_ENGINE_UNCONFIRMED`. What changed is that the
doubt is expressed as "your audio may not be reaching anyone" instead of "your
broadcast is over", because a stream that might be silent is recoverable and one that
never started is not.

## Call path

Untouched. No file under `src/calls/` or `src/core/` was modified in this addendum.
`livePublisherMedia.ts` was deliberately left alone so the identity test against
`realtimePublisherMedia.ts` still passes.

## Validation

`npm run typecheck` clean. `test:realtime-audio-critical` 16 suites / 350 tests.
`test:realtime-audio-architecture` 23 tests. Backend
`tests/protection/test_realtime_audio_architecture.py` 19 tests. New
`src/live/__tests__/liveAudioDegrade.test.ts` drives the guard through the wedge state
with no native bridge mocked in — the same condition as an unpatched device — and
asserts the repair runs, that a failed repair still throws, that a healthy recorder is
never torn down, and that the audience path never acquires a microphone.

**Still NOT performed: physical audible validation on two devices.** No hardware was
available. PHONE A speaks / PHONE B hears remains unproven, and nothing here should be
read as claiming otherwise.

## Addendum: PulseSoc iOS project rename and native Stripe payment sheet

This addendum records the protected `dependency_watch` files changed by the
marketplace payment-sheet work and the iOS Xcode project rename from
`PulseSocNative` to `PulseSoc`. It exists because the gate correctly refused the
change while `mobile-native/ios/Podfile` and `mobile-native/ios/Podfile.lock`
were unnamed. It does not reinterpret that refusal as an audio-runtime failure.

### Why the change is required

Two independent reasons converged on the same protected files:

1. **Native Stripe payment sheet.** Marketplace checkout previously could only
   hand the buyer a hosted Stripe Checkout URL, which the phone can only open in
   Safari. Collecting payment in-app requires `@stripe/stripe-react-native`,
   which is a native dependency and therefore lands in `package.json`,
   `package-lock.json`, and `Podfile.lock`.
2. **Xcode project rename.** The iOS project, target, scheme, workspace, and
   source group were renamed `PulseSocNative` -> `PulseSoc` to match the shipped
   product name. The Podfile target declaration had to follow, and `pod install`
   regenerated `Podfile.lock` and the `Pods-PulseSoc` support files.

### Which protected files changed

| File | Category | Change |
|---|---|---|
| `mobile-native/ios/Podfile` | dependency watch | Target renamed `PulseSocNative` -> `PulseSoc`; the explicit `project 'PulseSocNative.xcodeproj'` line removed so CocoaPods resolves the renamed project. One whitespace-only line. No pod added, removed, or pinned by hand. |
| `mobile-native/ios/Podfile.lock` | dependency lock | Regenerated by `pod install`. Adds `ExpoIap (4.7.2)` and `openiap (2.4.4)` — these come from `expo-iap`, which was **already** a dependency at HEAD and is unchanged; they appear now only because the lock was regenerated. `PulseNowPlaying (1.0.0)` is still present and still linked. |
| `mobile-native/package.json` | dependency watch | Adds `@stripe/stripe-react-native` 0.50.3. No other dependency changed. No script changed. |
| `mobile-native/package-lock.json` | dependency lock | The `@stripe/stripe-react-native` entry and its transitive entries only (+39 lines, 0 removed). |

### Audio-relevant analysis

No microphone permission, background-audio mode, `AVAudioSession` category or
option, Agora setting, audio feature flag, or audio-session owner changed in this
diff. Nothing under `src/calls/`, `src/core/`, `src/live/`, or `src/live-audio/`
was modified.

The one audio-adjacent risk worth stating plainly: `@stripe/stripe-react-native`
is a native SDK, and the Stripe payment sheet is a full-screen native modal. Any
native modal is a candidate for touching the shared audio session. This diff does
not configure the session, and Stripe's sheet is not an audio component, but that
is an argument from inspection, not from measurement. Presenting the payment
sheet during an active call or livestream has **not** been measured on hardware.

The Xcode rename is a build-system change, not a runtime one. It does, however,
mean every native artifact is rebuilt from a regenerated project, so the audio
native modules are relinked. Build verification below is what establishes that
the relink produced a working binary.

### Expected behavior change

Marketplace Buy-Now and cart checkout can present an in-app Stripe sheet when the
server returns a PaymentIntent client secret and the SDK is present in the
binary; otherwise they fall back to the existing hosted URL path. Call,
video-call, and livestream audio behavior is unchanged.

### Regression risk

Audio-runtime risk is low by inspection. Build risk is the real exposure: the
renamed project is newly generated, and two artifacts present in the old project
are absent from the new one — the `PulseSocNativeUITests` target (including
`PulseSocNativeCameraStudioQATests.swift`) and `ExportOptions-AppStore.plist`.
Neither is on the audio runtime path, but the missing export options file is a
release-packaging concern and the missing UI test target is lost camera QA
coverage. Both are recoverable from git history.

### Tests run

- `npm run typecheck`: clean.
- `npm run i18n:validate`: clean.
- `test:realtime-audio-critical`: 11 suites / 191 tests passed.
- `test:realtime-audio`: 18 suites / 310 tests passed.
- `test:realtime-audio-architecture` (native): 1 suite / 22 tests passed.
- Backend `tests/protection/test_realtime_audio_architecture.py`: 17 of 19 passed.
  **2 failed.** Both are stale LiveKit-era assertions left behind by
  `f93e7ce3 "chore(rtc): fire LiveKit"`: one requires
  `src/calls/useNativeCallRoom.ts` to import `../core/realtimePublisherMedia`,
  the other requires it to call `registerGlobals`. That file now delegates to
  `useAgoraCallRoom` and LiveKit is gone from the project, so both assertions
  describe an architecture that no longer exists. Verified pre-existing: the file
  is untouched by this change and both tests fail identically at HEAD. They need
  rewriting against Agora; this addendum does not claim they pass.
- Agora token/provider contract: 13 tests passed.
- New marketplace backend tests: 20 passed.
- Full `npm run verify` jest run: 200 of 203 suites, 3474 of 3479 tests passed.
  The 5 failures (`PulseBackground`, `CommerceSeparation`,
  `MarketplaceBuyerExperience`) were confirmed pre-existing by running those three
  suites in a clean worktree at HEAD with no working-tree changes, where they fail
  identically. This change neither causes nor fixes them.

### Physical validation required

**Not performed.** Owed before this can be treated as release-validated:

1. Paired host/viewer livestream audibility on two physical devices.
2. Call and video-call audio regression on a physical device.
3. Presenting the Stripe payment sheet while a call or livestream is active, and
   confirming audio survives both presentation and dismissal. This is new
   surface introduced by this change and has no prior baseline.

Installing and launching a signed build is not audible proof and is not claimed
as such.

### Rollback procedure

Revert this commit. That restores `PulseSocNative.xcodeproj`/`.xcworkspace`, the
old Podfile target, the UI test target, and `ExportOptions-AppStore.plist`, and
removes `@stripe/stripe-react-native`. Then run `pod install` to regenerate
`Pods-PulseSocNative`. No Agora, `AVAudioSession`, microphone-publication, or
backend flag rollback is required, because none of those changed.

### Addendum follow-up: Stripe SDK build patch

The first device build from the renamed project **failed** while compiling the
`stripe-react-native` pod:

```
StripeSwiftInterop.h:14: typedef NS_ENUM(NSUInteger, STPPaymentStatus);
stripe_react_native-Swift.h:627: SWIFT_ENUM_FWD_DECL(NSInteger, STPPaymentStatus)
error: enumeration redeclared with different underlying type 'NSInteger' (was 'NSUInteger')
```

This is an upstream defect in `@stripe/stripe-react-native` 0.50.3, not in
PulseSoc code. The Swift enum is backed by `Int`, so the generated header's
`NSInteger` is authoritative and the hand-written forward declaration's
`NSUInteger` is wrong. Xcode 26 rejects the mismatch where older toolchains did
not.

`npx expo install @stripe/stripe-react-native` re-resolves to 0.50.3, confirming
that is the version Expo SDK 54 pins — so upgrading is not the remedy. The fix
is `patches/@stripe+stripe-react-native+0.50.3.patch`, a one-line change of
`NSUInteger` to `NSInteger`. This follows the precedent already in the repo:
`patches/react-native+0.81.5.patch` exists for the same class of
toolchain-incompatibility build break. `patch-package` applies both on
postinstall.

Audio impact: none. The patch touches a payments header only. It is recorded here
because it changed `package.json`/`package-lock.json` handling and because a
third `patches/` entry now has to survive any future dependency reinstall — the
same fragility that already applies to the Hermes patch.

Build verification after the patch: device Release build `** BUILD SUCCEEDED **`,
0 errors.

Also noted and **not** done: `npx expo install` reported that
`@stripe/stripe-react-native` wants its Expo config plugin registered in
`app.config.js`. The checked-in Xcode project builds and links without it, so it
was left alone rather than changed blind during a deploy. It should be added
before the next `expo prebuild`, or prebuild will produce a project missing the
Stripe plugin's native configuration.

---

# Addendum — the audio protection suites were not being executed (2026-08-13)

Change: repair the protection runner and the Agora-era audio test files so the
suites that guard real-time audio actually run, and remove the seventh
`Audio.setAudioModeAsync` call site.
Base: `feaa3970` (`origin/main`)
Required label: `audio-critical-change`

## Why the change is required

This addendum changes no audio runtime code. It changes the instruments, and it
is filed because the instruments were reading zero.

Three separate findings, each of which made a green result meaningless:

**1. Six protection files never executed.** `tests/protection/_runner.py`
discovers module-level `test_*` callables only in files that end with a
`__main__` guard. The protection files added during the Agora migration —
`test_agora_cloud_recording.py`, `test_agora_direct_live_contract.py`,
`test_agora_mux_bridge.py`, `test_agora_replay_mux_contract.py`,
`test_agora_token_generation.py`, `test_live_social_distribution.py` — had no
guard. They were present in the directory, they were named in review, and they
contributed zero checks to every suite run since they landed. This is the exact
failure mode `_runner.py`'s own docstring was written about, reappearing in new
files.

**2. Adding the guard revealed the assertions had never been evaluated
anywhere.** Several of those suites are pytest-style and take a `monkeypatch`
argument. Called by the runner with no arguments they raised `TypeError`. So the
first honest run of `test_agora_token_generation` and `test_agora_cloud_recording`
— the files asserting who is granted a publish token and that no RTC token is
smuggled into a cloud-recording payload — happened as part of this change, not
when they were written. `_runner.py` now supplies the `setenv`/`delenv`/`setattr`
subset those files use, and restores every mutation in a `finally`.

**3. Two assertions in `test_realtime_audio_architecture.py` described an
architecture that no longer exists.** `f93e7ce3 "chore(rtc): fire LiveKit"`
removed LiveKit; these were left behind. One required
`src/calls/useNativeCallRoom.ts` to import `../core/realtimePublisherMedia`, the
other required it to call `registerGlobals`. The second is worse than stale — it
directly contradicted `realtimeAudioArchitecture.test.ts:202`, which asserts
LiveKit is *absent*. Two protection tests in the same repository demanded
opposite things, so at least one had to be failing at all times, and a
permanently-red check is a check nobody reads.

Separately, `mobile-native/src/video/videoMusicMix.ts` called
`Audio.setAudioModeAsync` directly, making seven call sites against an allowlist
`config/realtime-audio-protected-paths.json` freezes at six.

## Which feature required it

None. This is protection-apparatus repair. No user-facing behavior is intended
to change.

## Which protected files changed

| File | Category | Change |
|---|---|---|
| `tests/protection/test_realtime_audio_architecture.py` | critical_audio_tests | `test_calls_and_live_each_own_a_call_grade_publisher_coordinator` now checks both the legacy and the Agora adapter for calls (`useNativeCallRoom.ts`, `useAgoraCallRoom.ts`) and for live (`useLiveBroadcastRoom.ts`, `useAgoraLiveBroadcastRoom.ts`), asserting neither reaches into the other's engine modules. `test_livekit_sdk_never_configures_the_session_behind_the_coordinator` is renamed to `test_the_rtc_sdk_never_configures_the_session_behind_the_coordinator` and inverted: all four adapters must contain no `Audio.setAudioModeAsync(` and must not match `registerGlobals\|livekit`. |
| `tests/protection/test_agora_token_generation.py` | critical_audio_tests | Adds `import sys` and the repo-root `sys.path` bootstrap so the file imports outside pytest. Its `unittest.main()` entry point is unchanged; no assertion was altered. |
| `tests/protection/test_agora_direct_live_contract.py` | critical_audio_tests | Adds the `__main__` guard so the runner executes its 5 checks. No assertion was altered. |

Files changed in this range that are **not** on a protected path, listed for
completeness: `tests/protection/_runner.py`, `test_agora_cloud_recording.py`,
`test_agora_mux_bridge.py`, `test_agora_replay_mux_contract.py`,
`test_live_social_distribution.py`, `test_ios_build_version_contract.py`,
`mobile-native/src/core/reelsAudioSession.ts`,
`mobile-native/src/video/videoMusicMix.ts`,
`mobile-native/ios/PulseSoc.xcodeproj/project.pbxproj`, `.env.example`.

## The expo-av allowlist change, stated precisely

`videoMusicMix.configureVideoMusicMonitoring` no longer calls
`Audio.setAudioModeAsync` itself; it delegates to a new
`configureVideoCaptureMonitoringSession` in `core/reelsAudioSession.ts`, which is
already one of the six allowlisted files. **The mode object passed to
`setAudioModeAsync` is byte-identical to what `videoMusicMix` passed before** —
`allowsRecordingIOS: true`, `playsInSilentModeIOS: true`,
`staysActiveInBackground: false`, `interruptionModeIOS: MixWithOthers`,
`shouldDuckAndroid: false`, `playThroughEarpieceAndroid: false`. Nothing about
when or how the session is mutated changed; only the file the mutation lives in.

Call-site count after this change, verified by
`grep -rlF "Audio.setAudioModeAsync(" mobile-native/src`: **six** —
`calls/callSignalMedia.ts`, `core/pulseRadio.ts`, `core/reelsAudioSession.ts`,
`core/voiceMessagePlayback.ts`, `screens/ChatScreen.tsx`, `screens/MusicScreen.tsx`.

This is a real narrowing, not bookkeeping. The rule's remedy text says new media
playback must route through an existing allowlisted file, and moving the call
into `reelsAudioSession` puts a camera-capture session mutation where the audio
reviewers already look, instead of in a video-mixing helper they have no reason
to open.

## Expected behavior change

None. No AVAudioSession category, mode, or option value changed; no ownership
arbitration, publication path, engine module, or feature flag was touched. No
file under `src/calls/`, `src/core/` (other than the additive
`reelsAudioSession` export), `src/live/`, or `src/live-audio/` was modified.

## Regression risk

The runtime risk is confined to one indirection in the video-capture monitoring
path. If it were wrong, the symptom would be a music bed that does not duck or a
capture that cannot record — visible immediately in Reels capture, and not on
the call or livestream path at all.

The larger and more honest risk is the opposite direction: these suites are now
running for the first time. A check that has never executed is not known to be
correct merely because it passes on its first run. `test_agora_token_generation`
and `test_agora_cloud_recording` in particular should be treated as newly
written, not as long-standing coverage.

## Tests run

- `npm run test:realtime-audio-critical`: 11 suites / 191 tests passed.
- `npm run test:realtime-audio`: 18 suites / 310 tests passed.
- `npm run test:realtime-audio-architecture` (native): 1 suite / 22 tests passed.
- Backend `tests/protection/test_realtime_audio_architecture.py`: **19 tests OK**
  — previously 17 of 19, with the 2 stale LiveKit assertions failing. Those two
  are the ones rewritten above; this is the first green run of this file since
  `f93e7ce3`.
- Backend `pytest tests/protection/test_agora_token_generation.py
  tests/protection/test_agora_rtc_provider_contract.py`: 13 passed.
- Full protection suite (`scripts/protection/run_protection_suite.py`):
  **208 checks across 19 suites, exit 0**, up from 198. The +10 is not new
  assertions; it is the suites that previously executed nothing.
- `npx tsc --noEmit`: clean, no diagnostics.
- Native build verification: iOS Simulator and physical-device Release builds,
  recorded below.

`npx expo prebuild --platform ios --no-install` — named by the gate's own
checklist — was **deliberately not run**. The iOS project is checked in and
carries native customisations (`modules/pulse-now-playing/`, the WebRTC
AVAudioSession patch, the Stripe header patch) that a regenerated project drops.
A real `xcodebuild` against the checked-in project is stronger evidence than a
prebuild anyway, so that is what was done instead. The gate checklist should be
amended; it is currently asking for the one command this repository forbids.

## Physical validation required

**Not performed and not claimed.** Installing and launching a build is not
audible proof. `docs/realtime_audio_live_test_matrix.md` Groups A and B — PHONE A
speaks, PHONE B hears — remain owed, as they have been since the `src/live-audio/`
addendum.

This addendum adds no new physical requirement of its own, because it adds no new
runtime behaviour. The one thing worth checking opportunistically during the next
device session: record a Reel with a music bed and confirm the bed is still
audible in the monitor and present in the export, which is the only path the
`reelsAudioSession` indirection touches.

## Rollback procedure

Revert the four commits in this range. `reelsAudioSession.ts` loses one exported
function and `videoMusicMix.ts` regains its direct `setAudioModeAsync` call —
which puts the allowlist back at seven and re-breaks the gate, so the revert
should be paired with reverting the rule bump or with a different remedy. No
server flag, schema, or native dependency is involved.

# Addendum — Spatial Motion Console: `expo-sensors` dependency declaration (2026-08-14)

Change: `feature/spatial-console` @ `74a32e2c` (base `6e67e408`).
Required label: `audio-critical-change` (triggered by dependency watch only).

## Why the change is required

The Hybrid Spatial Motion Console adds optional phone-tilt navigation for the
Feed and Reels. Reading device orientation requires `expo-sensors`
(DeviceMotion). The gate fires because `mobile-native/package.json` is on the
dependency watch list — a manifest change, not an audio-path change.

## Which feature required it

Spatial Motion (tilt/parallax navigation), mission §, flag-gated behind
`EXPO_PUBLIC_SPATIAL_MOTION` and consent-gated behind onboarding. Sensors are
loaded via a safe dynamic require and never touched until the user opts in.

## Which protected files changed

| File | Category | Change |
|---|---|---|
| `mobile-native/package.json` | dependency watch | One line added: `"expo-sensors": "~15.0.7"`. No version changed on any existing dependency. `react-native-agora` stays `4.6.2`, `expo-av` stays `~16.0.8`, `expo` stays `~54.0.36`, `react-native` stays `^0.81.5`. No patch, Podfile, app.json, or eas.json change. |

`expo-sensors` contains no audio code: it wraps CoreMotion / SensorManager
(accelerometer, gyroscope, magnetometer, barometer, pedometer, DeviceMotion).
It does not link AVFoundation audio, does not touch `AVAudioSession`, and adds
no microphone entitlement. `package-lock.json` was NOT updated in this change
because the sandbox has no npm registry access (403); `npm install` must be run
on a networked machine before the next build, and the lockfile diff at that
point must show only the expo-sensors subtree.

## Expected behavior change

None to audio. Calls, livestream, Pulse Radio, and Reels audio paths are
untouched — no audio-session call sites, no LiveKit/Agora surface, no new
publication path, no new audio singleton. The `expo-av` allowlist count is
unchanged.

## Regression risk

Low and confined to build-time: the risk of adding a dependency is a lockfile
refresh disturbing transitive pins. Mitigated by adding only a tilde-pinned
package and by the rule that the upcoming `npm install` diff must be reviewed
to show only the expo-sensors subtree.

## Tests run

In-sandbox on 2026-08-14, all against `74a32e2c`:

- `npm run test:realtime-audio-critical` — 11 suites, 191 tests, pass
- `npm run test:realtime-audio` — 18 suites, 310 tests, pass
- `npm run test:realtime-audio-architecture` — 1 suite, 22 tests, pass
- `python3 -m unittest tests.protection.test_realtime_audio_architecture` — 19 tests, OK
- TypeScript compilation — `npx tsc --noEmit`, clean
- Full jest run — 221 suites, 3,764 tests, pass
- Backend token tests: **pytest is not installed in this sandbox and the
  package registry is blocked.** `python3 -m unittest
  tests.protection.test_agora_token_generation
  tests.protection.test_agora_rtc_provider_contract` ran 13 tests: 10 passed,
  3 failed — all three fail because the `agora_token_builder` pip package is
  absent in the sandbox (`_generate_agora_token` returns its 503
  `agora_token_builder_missing` error). The diff contains zero backend
  changes (`git diff 6e67e408..HEAD -- . ':!mobile-native' ':!docs'` is
  empty), so these are environmental, not regressions. They must be re-run
  green in CI, which has the package.
- Native build verification: not run in this sandbox (no macOS toolchain);
  owed to CI/EAS as with prior addenda.

## Physical validation required

No new audio behavior is introduced, so this addendum adds no new audible
requirement. The standing Groups A/B live-audio matrix debt from earlier
addenda remains owed and is unaffected by this change.

## Rollback procedure

Remove the `expo-sensors` line from `package.json` (and its lockfile subtree
once installed) and revert `74a32e2c`. Behavioral rollback needs no revert at
all: every spatial flag defaults OFF, and motion additionally requires user
onboarding — see `docs/spatial-console-rollback.md`.

### Correction (2026-08-15): expo-sensors pin aligned to SDK 54 manifest

`expo/bundledNativeModules.json` for the installed SDK 54 expects
`expo-sensors: ~15.0.8`; the original declaration line said `~15.0.7`. The pin
in `mobile-native/package.json` is corrected to `~15.0.8` in this change. Both
ranges resolve inside 15.0.x; no other dependency line changed; the lockfile
remains not-yet-updated because the sandbox registry block persists (verified
again 2026-08-15: `403 blocked-by-allowlist`). Everything else in the addendum
above stands unchanged.
