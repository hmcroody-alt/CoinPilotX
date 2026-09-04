# Multi-guest Livestream — final report

Stages 59 and 60 of the multi-guest livestream mission.

Branch `main`, HEAD `a7490add`. Nothing has been staged, committed, pushed or
deleted by this mission — see Stage 57 below.

---

## Verdict

**The decisions are proven. The media path is not.**

Every rule this mission was asked to establish — one channel, one engine owner,
server-authoritative roles, a broadcast that survives guests arriving and
leaving, an audience that cannot promote itself — is now encoded in executable
tests that run the real modules and pass: 459 native checks, 231 backend checks
under pytest, and 308 checks across 27 suites under the repository's own CI
protection runner.

None of that demonstrates that sound comes out of a phone. The one function that
creates the Agora engine, sets the client role, configures the encoder and
publishes — `useAgoraLiveBroadcastRoom.connect()` — has no executable coverage
anywhere in this repository, for a structural reason given below. Every physical
device gate is therefore `NOT OBSERVED`, and the real-time audio hard lock has
deliberately **not** been re-baselined.

The honest summary is: this mission finished the half of the work that can be
proved without hardware, and wrote down precisely what the other half requires.

---

## Stage 58 — automated gates

Each gate reported individually, with the command that produced it.

| Gate | Command | Result |
|---|---|---|
| TypeScript | `npx tsc --noEmit -p tsconfig.json` | **PASS** — clean, no output |
| Live unit + scenario suites | `npx jest src/live --silent --maxWorkers=2` | **PASS** — 28 suites, 457 passed + 2 todo = 459, 8.3s |
| API suites | `npx jest src/api --silent --maxWorkers=2` | **PASS** — 78 suites, 1348 passed, 13.7s |
| Live studio screen | `npx jest src/screens/__tests__/LiveStudioScreen.test.tsx` | **PASS** — 13 passed |
| Full `src/screens` scope | `npx jest src/screens` | **NOT OBSERVED** — exceeds this environment's 45-second command ceiling on every attempt, including a backgrounded run. Not a failure and not a pass. `LiveHostSessionScreen.tsx` was edited by this mission, so this gate should be run before merge. |
| Backend protection (pytest) | `python3 -m pytest tests/protection -q --tb=no` | **PASS** — 231 passed, 35.6s |
| Backend protection (CI runner) | `python3 scripts/protection/run_protection_suite.py` | **PASS** — 308 checks across 27 suites |
| Agora token contract | `pytest tests/protection/test_agora_token_generation.py tests/protection/test_agora_rtc_provider_contract.py` | **PASS** — 13 passed |
| i18n | `node scripts/validate-i18n.mjs` | **PASS** — 11 locales, 4360/4360 keys each; 4 pre-existing advisory warnings |
| Realtime-audio protection gate | `python3 scripts/realtime_audio_change_gate.py --base origin/main --head HEAD` | **PASS for this mission** — see note below |

On the last row: the gate flags `mobile-native/src/api/calls.ts` and
`mobile-native/src/screens/CallScreen.tsx`, both pre-existing from branch commit
`6645171c` and both outside this mission's authorization. No Live module and no
`bot.py` line introduced by this mission triggered it. The mission's
protected-path authorization covered the livestream RTC path only, and it was
not exceeded.

### Two gate results that were misleading before they were investigated

**The three `test_agora_token_generation.py` failures seen in an earlier session
were not a Stage 4 defect.** The cause was `agora-token-builder`
(`requirements.txt` line 19) not being installed in this sandbox. Installed, all
thirteen tests pass. The token contract was never broken; the environment was.
This is worth recording because a token-contract failure is exactly the kind of
result that gets believed on sight.

**`test_live_moderation_authority.py` passed pytest and failed the repository's
own CI runner.** The protection runner executes each suite as `python3 <file>`,
not through pytest, so there is no rootdir conftest to put the repository on
`sys.path` — the four tests that `from services import live_participants`
errored under CI while passing locally. Fixed this session by inserting the
repository root on the path inside the file. A sweep confirmed no other
protection suite has the same shape. This is the worst class of defect available
in a test harness, because the run people trust is the one that lies.

---

## Stage 59 — device gates

All gates below are `NOT OBSERVED`. That is not `FAIL`: nothing has been shown
to be broken and nothing has been shown to work. The full script, including
hardware requirements, build instructions and a copy-paste reporting template,
is in `MULTI_GUEST_LIVE_DEVICE_ACCEPTANCE.md`.

Per the mission's explicit instruction, these are **not** collapsed into a
single verdict. The interesting outcome of a device run is a partial pass —
video correct and audio one-directional, for instance — and a summary line
destroys exactly that information.

### Stage 50 — single-host Live, unchanged (regression)

| Gate | Verdict |
|---|---|
| 50.1 Host live, viewer sees video within 5s and hears host | NOT OBSERVED |
| 50.2 60 seconds continuous speech, no dropout/echo/restart | NOT OBSERVED |
| 50.3 Camera front↔rear twice, video continues, audio uninterrupted | NOT OBSERVED |
| 50.4 Host backgrounds 20s and returns, Live never ended | NOT OBSERVED |
| 50.5 Host ends, viewer sees end, replay reel within 5 min | NOT OBSERVED |
| 50.6 `MULTI_GUEST_LIVE_ENABLED=false`, indistinguishable from 50.1 | NOT OBSERVED |

### Stage 51 — host plus one guest

| Gate | Verdict |
|---|---|
| 51.1 Invite arrives within 5s, exactly once | NOT OBSERVED |
| 51.2 Guest sees local preview before their tile is public | NOT OBSERVED |
| 51.3 Two tiles on all three devices, guest tile never black | NOT OBSERVED |
| 51.4 Host speaks — guest hears, viewer hears | NOT OBSERVED |
| 51.5 Guest speaks — host hears, viewer hears | NOT OBSERVED |
| 51.6 Alternating speech 60s, no echo, no dropout | NOT OBSERVED |
| 51.7 **Host's stream never froze, blacked out or restarted** | NOT OBSERVED |
| 51.8 Host tile visibly larger; does not read as a two-person call | NOT OBSERVED |
| 51.9 Guest leaves — **the Live continues** | NOT OBSERVED |
| 51.10 Host ends; replay reel shows both tiled | NOT OBSERVED |

Gate 51.7 is the mission's central claim and the script assigns it a dedicated
continuous observer whose only job is to say whether the picture ever went away.

### Stage 52 — host plus two guests

| Gate | Verdict |
|---|---|
| 52.1 Three tiles on all three devices | NOT OBSERVED |
| 52.2 Audibility matrix — six directed pairs | see below |
| 52.3 Active-speaker highlight follows speaker, tiles do not move | NOT OBSERVED |
| 52.4 Existing guest does not flicker or re-mount when a third joins | NOT OBSERVED |
| 52.5 Host still featured, full-width, at three publishers | NOT OBSERVED |
| 52.6 Host mutes a guest; that guest silent to all, others unaffected | NOT OBSERVED |
| 52.7 Host removes a guest; their camera and mic stop, others undisturbed | NOT OBSERVED |
| 52.8 Last guest leaves — **Live continues, stage returns to full-bleed solo** | NOT OBSERVED |

Gate 52.2, six separate results — "audio works" is not a result, because the
failure this catches is one-directional:

| Direction | Verdict |
|---|---|
| A → B | NOT OBSERVED |
| A → C | NOT OBSERVED |
| B → A | NOT OBSERVED |
| B → C | NOT OBSERVED |
| C → A | NOT OBSERVED |
| C → B | NOT OBSERVED |

### Stage 53 — chaos and endurance

| Gate | Verdict |
|---|---|
| 53.1 Ten join/leave cycles in varied order; no restart, no ghosts | NOT OBSERVED |
| 53.2 Guest airplane mode 20s — rejoins as **one** participant | NOT OBSERVED |
| 53.3 Host airplane mode 20s — Live survives the grace period | NOT OBSERVED |
| 53.4 Host offline beyond 90s — Live ends cleanly, no orphaned recording | NOT OBSERVED |
| 53.5 Viewer joins mid-stream, sees the same arrangement as the stage | NOT OBSERVED |
| 53.6 Comments never clear, duplicate or scroll backwards | NOT OBSERVED |
| 53.7 Reactions from a viewer reach everyone | NOT OBSERVED |
| 53.8 Viewer count never doubles, never negative, never counts stage twice | NOT OBSERVED |
| 53.9 Three publishers, 30 continuous minutes, no thermal or memory failure | NOT OBSERVED |
| 53.10 **Incoming audio call during a Live** — clean audible failure, not silence | NOT OBSERVED |
| 53.11 Screen share during a three-publisher Live | NOT OBSERVED |
| 53.12 Live music mix during a three-publisher Live | NOT OBSERVED |

Gate 53.10 is the gate most likely to fail. It is the only place where two
subsystems contend for the single `AVAudioSession`, and it is the direct test of
this mission's audio rules against the pre-existing call system. A clean audible
failure is a pass. A silent phone is not.

### Why these cannot be substituted with more automated tests

Two structural reasons, not incidental ones.

`useAgoraLiveBroadcastRoom` reaches the SDK through
`await import("react-native-agora")`, and `babel-preset-expo` under `jest-expo`
does not transpile a dynamic import — the call throws
`ERR_VM_DYNAMIC_IMPORT_CALLBACK_MISSING_FLAG` before any assertion runs. The
hook's entire `connect()` path is pinned by source-text assertions in
`tests/protection/` for its *wiring*, and by the device script alone for its
*behaviour*.

And audio is a device property. One `AVAudioSession`, one microphone, one
speaker, and an echo canceller whose behaviour depends on the physical distance
between two handsets. The failure mode this mission's audio rules exist to
prevent — a second engine stealing the session — produces green tests and silent
phones.

---

## Stage 54 — the hard lock

**NOT UPDATED, deliberately.**

`config/realtime-audio-protected-paths.json` still records
`verified_commit = ce03e160eaf4649a8e02bc3b609a3182ca9d3859` (2026-08-02).

Advancing that pointer is a claim that someone put phones in a room and heard
sound. Moving it on the strength of a passing automated suite would convert "we
tested the decisions" into "we verified the audio", which is the precise
misrepresentation the hard-lock mechanism exists to prevent. The four-step
re-baseline procedure, to be performed only after every Stage 50–53 gate reports
`PASS`, is in `MULTI_GUEST_LIVE_DEVICE_ACCEPTANCE.md`.

---

## Stage 57 — git safety

No `git add -A`. No `reset --hard`. No `clean -fd`. No force push. In fact
nothing was staged or committed at all: git is effectively read-only in this
environment (`.git/index.lock`: Operation not permitted). Every change sits in
the working tree for a human to stage explicitly.

---

## What changed

Modified: `.env.example`, `bot.py`, `mobile-native/app.json`,
`src/api/live.ts`, `src/live/agoraLiveTelemetry.ts`, `src/live/liveMusicMixing.ts`,
`src/live/liveSession.ts`, `src/live/useAgoraLiveBroadcastRoom.ts`,
`src/screens/LiveHostSessionScreen.tsx`,
`services/pulsesoc_communications_engine.py`,
`tests/protection/test_agora_token_generation.py`, plus two existing Live test
files.

New runtime modules, all under `mobile-native/src/live/`: `liveParticipantRegistry`,
`liveSessionLifecycle`, `liveSeatReconciliation`, `liveStageLayout`,
`liveAudioMatrix`, `liveMediaOwnership`, `liveGuestStage`, `liveEventContinuity`,
`liveStreamQuality`, `liveTelemetryPrivacy`, and the `LiveStage` /
`LiveModerationSheet` components. New backend module `services/live_participants.py`.

New tests: thirteen native suites and six backend suites, including
`multiGuestBroadcastScenarios.test.ts` (Stages 42–49) and
`liveTelemetryPrivacy.test.ts` (Stage 55).

### Stage 55 — a live defect found and fixed

`emitAgoraLiveEvent` logged a field called `uid`, and in this codebase
`_agora_uid(user_id) == user_id` — the Agora uid *is* the PulseSoc account id.
Roughly twenty-seven telemetry call sites in the hook were writing account
identifiers into the device log, where they reach the system log and any
attached crash reporter. Worse than the identifier is that it is stable across
every Live a person has ever appeared in, which turns debug logs into a social
graph.

Fixed at the single chokepoint — the emitter — rather than at the call sites,
specifically so that a future author adding an event under deadline cannot
reintroduce it. Pseudonymisation rather than redaction, because a redacted log
cannot tell guest three from guest four, which is what the telemetry is for.
The tag is salted with the live id and a per-process value, so the same person
in two Lives, or on two devices, gets two tags. That last part costs us the
ability to join logs across devices during an investigation, and it is accepted
deliberately: a scheme that lets us do that is one that lets anyone else do it
too.

---

## Findings carried forward

**Neither the Agora Live path nor the Agora call path claims a JavaScript audio
lease.** This is by design per the manifest's `required_lease_discipline`
reasoning. Recorded as an observation, not a defect, but it is the thing an
operator should know before debugging gate 53.10.

**Live cloud recording is not feature-flagged.** It runs on every Live
unconditionally. Pre-existing, deliberately left alone by this mission (the
instruction was to audit and report cost, not to enable or change anything), but
it is a decision worth making explicitly before rollout rather than
inheriting. Cost analysis is in `MULTI_GUEST_LIVE_RECORDING_AUDIT.md`; the short
version is that Agora bills on aggregate resolution across every subscribed
stream, so the publish-side encoder ladder is the billing lever, and the ladder
now steps down as the stage fills to keep a six-publisher Live off the 2K tier.

**Existing `cohost` rows.** `bot.py` now defaults new stage entries to
`ROLE_GUEST` instead of `ROLE_COHOST`. Rows written before this change are
stored as `'cohost'` and will carry moderation authority under the new
enforcement. Judged acceptable — the population is small and the authority is
bounded, and a co-host still cannot end a broadcast — but it is a deploy-time
consideration, not a non-issue.

**`ios.buildNumber` moved 21 → 22** in `mobile-native/app.json` to match the
authoritative `CURRENT_PROJECT_VERSION` in the checked-in pbxproj. Do **not**
run `expo prebuild` — it discards the Hermes build fix and the LiveKit
`AVAudioSession` patch, and losing the latter would invalidate every audio gate
in a way that is not visible until a call and a Live overlap.

**`.env.example` gained three keys this mission did not introduce**
(`CALL_MAX_AUDIO_PARTICIPANTS`, `CALL_MAX_VIDEO_PARTICIPANTS`,
`PRIVATE_OFFICE_GRANT_TTL_SECONDS`), purely so the environment-contract gate
passes.

**`test_agora_token_generation.py::test_native_agora_live_quality_and_publish_confirmation_contract`**
no longer asserts a fixed 720×1280 in the adapter, because the adapter now
applies the encoder ladder. The protected property moved to `liveStreamQuality.ts`
and is asserted there.

## Housekeeping — needs a human

File deletion is not permitted in this environment. Four scratch artefacts need
manual removal before merge:

```
rm mobile-native/src/live/__tests__/zz_dbg.test.ts
rm mobile-native/src/live/__tests__/zz_dbg2.test.ts
rm -r mobile-native/src/media/__dbg__
rm mobile-native/tsconfig.probe.json
```

`zz_dbg.test.ts` still appears in the `src/live` jest run and is counted in the
459 figure above.

---

## Definition of done

The mission's own criteria, answered one at a time.

| Criterion | State |
|---|---|
| Host and guests publish into ONE canonical Agora session | Proven in code and tests; unproven on device |
| Audience receives every authorized publisher | Proven in tests; unproven on device |
| Guest join/leave does not restart the Live | Proven — a 100-step randomised scenario produces no disruptive host seat action |
| Every expected participant audible | **Requires gate 52.2. NOT OBSERVED.** |
| Every expected stream visible | **Requires gates 51.3, 51.7, 52.1. NOT OBSERVED.** |
| Audience cannot self-promote | Proven — server-authoritative, static guards over `bot.py` |
| Existing single-host Live still works | Proven in tests; **regression gate 50.1–50.6 NOT OBSERVED** |
| Calls remain intact | Proven by the protection suite; **contention gate 53.10 NOT OBSERVED** |
| Physical devices prove the media path | **NOT OBSERVED** |

The mission does not pass because multiple faces appear. It has not passed. It
is blocked on three physical iPhones and the script that tells someone what to
do with them.
