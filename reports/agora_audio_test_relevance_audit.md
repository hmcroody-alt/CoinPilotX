# Agora audio test relevance audit

**Date:** 2026-09-04
**Branch:** `integration/all-work-20260904`
**Scope:** every suite the real-time audio manifest, the audio npm scripts, or
`tests/protection/` treats as audio coverage.
**Result of the run this audit describes:** 45 native suites, 746 tests, 0 failures;
5 backend protection suites, all passing.

## Why this audit exists

The number "746 passing audio tests" is currently read as a statement about the
shipping app. It is not one. A test is only evidence about the product if the
module it exercises is on a path the product executes, and a large block of these
suites is green about code the app no longer loads.

That is not a claim about test quality — several of the suites in class D below
are the best-written files in the repository. It is a claim about *what they are
evidence of*, which is the question the manifest, the change gate and the release
declaration all silently depend on.

## Method

Reachability was computed, not guessed. A breadth-first walk of the real import
graph from the two Metro entry points (`App.tsx`, `index.ts`), resolving
relative and `src/`-rooted specifiers including dynamic `import()` and
`require()`, reaches **612 modules**. A module absent from that set cannot run in
the app under any code path, regardless of how thoroughly it is tested.

Every module that was ambiguous under a filename grep was resolved this way.
**There are no UNKNOWN entries in this audit.**

## Classes

| Class | Meaning |
|---|---|
| **A** | Runtime coverage on the shipped path. Exercises a module the app loads and that can itself alter engine lifecycle, membership, publication, subscription, mic ownership, audio scenario or role transitions. |
| **B** | Decision coverage on the shipped path. A pure policy module that an engine owner consults. Proves the *answer* is right; cannot prove the owner still asks. |
| **C** | Structural coverage. Source-, manifest- or graph-level assertions. Proves shape, never behaviour. |
| **D** | Orphaned target. The module under test is not reachable from an entry point. Green, and not evidence about the product. |
| **E** | Not audio-relevant. UI, presentation, privacy or non-RTC concerns. Correctly scoped; simply not audio coverage. |

## A + B — coverage that describes the running product

Reported separately from C and D, because these are the only classes whose
passing state is an argument that Live and Calls audio work.

### Class A — runtime, shipped path

| Suite | Tests | Target |
|---|---:|---|
| `calls/__tests__/callSessionStore.test.ts` | 10 | `callSessionStore.ts` — one of the app's two `createAgoraRtcEngine` owners |
| `calls/__tests__/useAgoraCallRoom.test.ts` | 2 | the call room adapter over that owner |
| `calls/__tests__/callKitBridge.test.ts` | 6 | CallKit ↔ session bridge |
| `calls/__tests__/callSignalMediaReentrancy.test.ts` | 3 | `callSignalMedia.ts` tone/media re-entrancy |
| `calls/__tests__/callToneLifecycle.test.ts` | 19 | `callToneLifecycle.ts`, consumed by the engine owner and `CallScreen` |
| `calls/__tests__/callerAcceptanceSync.test.ts` | 13 | session store + CallKit + tone, driven together |
| `calls/__tests__/callerPollAppStateGate.test.ts` | 5 | the same, across an `AppState` transition |
| `calls/__tests__/callParticipants.test.ts` | 9 | `callParticipants.ts`, consumed by the engine owner |
| **Total** | **67** | |

**Every class A suite is a Calls suite.** There is no class A coverage of Live.
That is not an oversight in this audit; it is the finding in §"The Live gap".

### Class B — decision layer, shipped path

Each of these modules is consulted by `useAgoraLiveBroadcastRoom` — the other
`createAgoraRtcEngine` owner — directly or one hop away.

| Suite | Tests | Target | Consulted by |
|---|---:|---|---|
| `live/__tests__/liveSeatReconciliation.test.ts` | 11 | `liveSeatReconciliation.ts` | hook, in `connect` |
| `live/__tests__/liveSessionLifecycle.test.ts` | 34 | `liveSessionLifecycle.ts` | via `liveParticipantRegistry` |
| `live/__tests__/liveParticipantRegistry.test.ts` | 30 | `liveParticipantRegistry.ts` | hook + `liveAudioMatrix` |
| `live/__tests__/liveAudioMatrix.test.ts` | 24 | `liveAudioMatrix.ts` | hook |
| `live/__tests__/liveEchoControlWiring.test.ts` | 10 | `nextEchoScenario` transitions | hook, `setStagePublisherCount` |
| `live/__tests__/liveStreamQuality.test.ts` | 23 | `liveStreamQuality.ts` | hook, `publisherVideoProfile` |
| `live/__tests__/liveMusicMixing.test.ts` | 8 | `liveMusicMixing.ts` | hook, mic + mixing gain |
| `live/__tests__/liveSession.test.ts` | 27 | `liveSession.ts` credential contract | hook |
| `live/__tests__/cohostPublishGate.test.ts` | 6 | `canConnectAsCohostPublisher` | Live host screen |
| `live/__tests__/multiGuestBroadcastScenarios.test.ts` | 36 | all of the above, run together over a simulated session | — |
| **Total** | **209** | | |

**A + B = 276 tests across 18 suites.**

## C + D — the remainder

### Class C — structural

Real value, and a different kind of value: these are the only checks that fire on
a file the Jest runner cannot load.

| Suite | Tests | What it pins |
|---|---:|---|
| `core/__tests__/realtimeAudioArchitecture.test.ts` | 22 | import boundaries, forbidden-API allowlists, by reading source |
| `tests/protection/test_realtime_audio_architecture.py` | — | manifest integrity, lease discipline, dependency lock |
| `tests/protection/test_realtime_audio_gate_coverage.py` | 14 (+27 subtests) | runs the **real gate** as a subprocess and asserts its exit codes and JSON |
| `tests/protection/test_agora_direct_live_contract.py` | 10 | the hook's Agora wiring, at source level |
| `tests/protection/test_agora_rtc_provider_contract.py` | — | Agora-only provider, server-only certificate |
| `tests/protection/test_agora_token_generation.py` | — | token minting |

`test_realtime_audio_gate_coverage.py` is deliberately not a manifest reader: it
invokes `scripts/realtime_audio_change_gate.py` with synthetic changed-file lists
and asserts what the gate concludes. A test that read the JSON and looked for
filenames would only prove the manifest says what the manifest says.

### Class D — orphaned targets

**Not reachable from `App.tsx` or `index.ts`.** These suites pass, and their
passing is not evidence about the shipping app.

| Suite | Tests | Orphaned target |
|---|---:|---|
| `core/__tests__/realtimeAudioEngine.test.ts` | 48 | `core/realtimeAudioEngine.ts` |
| `core/__tests__/realtimeAudioContracts.test.ts` | 42 | engine + ownership policy + mic publisher |
| `core/__tests__/mediaAdaptationController.test.ts` | 34 | `core/mediaAdaptationController.ts` |
| `core/__tests__/realtimeAudioInvariants.test.ts` | 18 | `core/realtimeAudioInvariants.ts` |
| `core/__tests__/realtimeAudioNative.test.ts` | 16 | `core/realtimeAudioNative.ts` |
| `core/__tests__/audioOwnershipPolicy.test.ts` | 14 | `core/audioOwnershipPolicy.ts` |
| `core/__tests__/realtimeAudioTelemetry.test.ts` | 11 | `core/realtimeAudioTelemetry.ts` |
| `core/__tests__/realtimeAudioStateMachine.test.ts` | 4 | `core/realtimeAudioStateMachine.ts` |
| `calls/__tests__/callAudioOwnershipRegression.test.ts` | 8 | imports `core/realtimeAudioEngine` |
| `live/__tests__/liveAudioRecovery.test.ts` | 20 | `live/liveAudioRecovery.ts` |
| `live/__tests__/liveAudioTelemetry.test.ts` | 18 | `live/liveAudioTelemetry.ts` |
| `live/__tests__/liveAudioPublisher.test.ts` | 9 | `live/liveAudioPublisher.ts` |
| `live/__tests__/liveRuntime.test.ts` | 7 | `live/liveRuntime.ts` |
| `live/__tests__/liveAudioDegrade.test.ts` | 4 | `live-audio/liveAudioEngine.ts` |
| `live/__tests__/liveAudioTrace.test.ts` | 3 | `live/liveAudioTrace.ts` |
| `live/__tests__/liveMediaOwnership.test.ts` | 18 | `live/liveMediaOwnership.ts` — built, not yet mounted |
| `live/__tests__/liveGuestStage.test.ts` | 26 | `live/liveGuestStage.ts` — built, not yet imported |
| `live/__tests__/LiveModerationSheet.test.tsx` | 17 | `LiveModerationSheet.tsx` — not mounted by any screen |
| **Total** | **317** | |

Two distinct situations are combined in that table and must not be confused:

1. **Superseded audio core** (the `core/realtimeAudio*`, `live-audio/*`,
   `liveAudioPublisher`, `liveAudioRecovery`, `liveAudioTrace`,
   `liveAudioTelemetry`, `liveRuntime`, `mediaAdaptationController` family —
   **265 tests**). The Agora migration moved Live and Calls onto
   `useAgoraLiveBroadcastRoom` and `callSessionStore`; this family is the
   pre-migration architecture, still built, still tested, no longer loaded.
   `liveAudioPublisher.ts` is the clearest case: it exists to forward to
   `live-audio/liveMicrophonePublisher`, and nothing imports the forwarder.
2. **Multi-guest UI not yet wired** (`liveMediaOwnership`, `liveGuestStage`,
   `LiveModerationSheet` — **61 tests**). Built behind
   `MULTI_GUEST_LIVE_ENABLED`, which defaults to false. These are *ahead of* the
   product rather than behind it, and are expected to become reachable.

**No file in either group was deleted, disabled or skipped by this audit.** The
mission is preservation; the correct response to "this is not evidence of what we
thought" is to say so, not to remove it. Removal is filed as a follow-up so it can
be done deliberately, with a declaration, by someone who has confirmed group 1 is
genuinely dead rather than lazily loaded by a path this walk missed.

### Class E — not audio coverage

`liveStageLayout` (27) · `liveTelemetryPrivacy` (32) · `liveEventContinuity` (27) ·
`liveStudioReadiness` (24) · `LiveStage` (10) · `liveChatModeration` (5) ·
`MinimizedCallBanner` (5) · `rtcVideoPresentation` (1) — **131 tests.**

Correctly scoped and worth keeping; simply not evidence about audio.
`liveTelemetryPrivacy` is the one worth naming: it protects a real invariant — the
Agora uid *is* the PulseSoc user id, so every uid must pass through
`sanitizeLiveTelemetry` — but that is a privacy property, not an audio one, and an
audio-regression gate is the wrong instrument for it.

## The Live gap

**There is no behavioural test of `useAgoraLiveBroadcastRoom.ts`, and there cannot
be one under the current Jest configuration.**

This is not inferred from a comment. It was verified: a probe test that mocks
`react-native-agora` and does nothing but `await import("react-native-agora")`
fails with

```
TypeError: A dynamic import callback was invoked without --experimental-vm-modules
```

before any assertion runs. The hook resolves the SDK that way in five places, so
it cannot be rendered in a unit test at all. Its 461 lines — the engine
lifecycle, the join, the role transitions, the scenario change, the mic gain —
have no runtime coverage.

What exists instead, and what each part is worth:

- **The decisions** are extracted into pure modules and are covered by the 209
  class B tests. This is genuinely strong: `reconcileLiveSeat` is provably unable
  to return `rejoin` for anything but a changed channel or uid.
- **The wiring** — whether the hook still *asks* — is covered at source level by
  the five assertions added to `tests/protection/test_agora_direct_live_contract.py`
  on 2026-09-04. They pin the four seams where a correct decision reaches, or
  fails to reach, the engine: that only `rejoin` can reach the teardown, that a
  role change never calls `joinChannel`, that `adjustRecordingSignalVolume` is
  never handed a raw UI level, and that a scenario change always restores mixing.
  Each was checked against an in-memory mutation of the source to confirm it
  actually fails when the invariant is broken.
- **Nothing else.** Source assertions are weaker than behavioural ones and are
  not a substitute. Live audio's real gate remains the physical validation in
  §7 of `reports/realtime_audio_verified_baseline.md`.

Closing this properly means running Jest with `--experimental-vm-modules`, or
injecting the SDK rather than dynamically importing it. Both are runner- or
architecture-level changes with a blast radius across 344 suites, which is a
deliberate piece of work and not a consolidation fix. Filed as a follow-up.

## Summary

| Class | Suites | Tests | Is it evidence about the shipping app? |
|---|---:|---:|---|
| A — runtime, shipped | 8 | 67 | Yes, for Calls |
| B — decisions, shipped | 10 | 209 | Yes, for Live's decisions — not its wiring |
| **A + B** | **18** | **276** | |
| C — structural | 6 | — | Shape only, including the only reach into the hook |
| D — orphaned target | 18 | 317 | **No** |
| E — not audio | 8 | 131 | Out of scope by design |

276 of 746 native tests — **37%** — are evidence about audio in the app as
shipped. The other 63% is not worthless; it is structural coverage, coverage of a
superseded architecture, coverage of a feature waiting behind a flag, and
correctly-scoped non-audio coverage. It is simply not what the headline number
has been read to mean.

## Follow-ups filed

1. Decide the fate of the superseded audio core (265 tests, group 1 above):
   confirm it is dead, then remove modules and suites together, with a
   declaration. Until then it is protected and inert.
2. Make `useAgoraLiveBroadcastRoom` testable — `--experimental-vm-modules` or SDK
   injection — and convert the five source assertions into behavioural ones.
3. Re-run this audit when `MULTI_GUEST_LIVE_ENABLED` is switched on; 61 tests
   move from D to A/B on that day and the manifest notes for
   `liveMediaOwnership.ts` should be updated to match.

---

# Appendix — Agora-only dependency audit (item 7.4)

Method: `grep -rn` over `mobile-native/src` for `*.ts`/`*.tsx`, excluding
`__tests__`. An **owner** is a file that calls the named engine method on an
`IRtcEngine` instance. A file that renders `RtcSurfaceView`, or that calls a
hook's own wrapper (e.g. `room.switchCamera()` in `LiveHostSessionScreen.tsx`),
is a **consumer**, not an owner — it cannot reach the engine except through one
of the two owners below.

```
AGORA ENGINE OWNER COUNT: 2

APPROVED ENGINE OWNERS:
  mobile-native/src/calls/callSessionStore.ts          (1:1 calls)
  mobile-native/src/live/useAgoraLiveBroadcastRoom.ts  (Live broadcast)

UNEXPECTED ENGINE OWNERS: 0        (0 REQUIRED — met)

MIC OWNERS: 2
  callSessionStore.ts          muteLocalAudioStream, publishMicrophoneTrack (join opts)
  useAgoraLiveBroadcastRoom.ts muteLocalAudioStream, publishMicrophoneTrack
                               (join opts + updateChannelMediaOptions),
                               adjustRecordingSignalVolume x3 — every call site
                               routed through liveMixLevelToAgoraVolume(), pinned
                               by test_microphone_gain_is_never_set_from_a_raw_ui_level

CAMERA OWNERS: 2
  callSessionStore.ts          startPreview, muteLocalVideoStream, switchCamera,
                               publishCameraTrack (join opts)
  useAgoraLiveBroadcastRoom.ts startPreview x2, muteLocalVideoStream, switchCamera,
                               publishCameraTrack (join opts + updateChannelMediaOptions)
  (LiveHostSessionScreen.tsx calls room.switchCamera() — the hook's API, not the engine.)

JOINCHANNEL OWNERS: 2
  callSessionStore.ts:731
  useAgoraLiveBroadcastRoom.ts:345
  Teardown is symmetric and equally confined: leaveChannel()/release() appear only
  in callSessionStore.ts:574-575 and useAgoraLiveBroadcastRoom.ts:138.

DUPLICATE PUBLICATION PATHS: 0     (0 REQUIRED — met)
  Neither owner can be mounted by the other's surface: calls publish from the
  call session store, Live publishes from the broadcast hook, and each has
  exactly one joinChannel and one teardown. Role changes inside Live are done
  with setClientRole + updateChannelMediaOptions (useAgoraLiveBroadcastRoom.ts:196,
  210) and never by re-joining — pinned by
  test_a_role_change_stays_inside_the_session.

RTC PROVIDER DEPENDENCIES: 1
  react-native-agora 4.6.2 — the only RTC package in mobile-native/package.json.
  No @livekit/*, no react-native-webrtc, no Twilio, no Daily.
  Zero LiveKit references remain in shipping source; the matches that survive are
  confined to test files, and are either redaction fixtures asserting a URL is
  scrubbed or suites over the superseded audio core already classified D above.
```
