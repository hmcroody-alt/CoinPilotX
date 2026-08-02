# PulseSoc Elite Live Media Quality — Consolidated Report

**Mission:** Elite live media quality across eight real-time surfaces
**Baseline of record:** `realtime-audio-stable-v1` → `ce03e160eaf4649a8e02bc3b609a3182ca9d3859`
**Base commit for this work:** `985c3886944fdcb343dc3a84915d139dad553cbf`
**Date:** 2026-08-02
**Change declaration:** `reports/realtime_audio_change_declaration.md`

---

## Verdict

**PARTIAL.**

Every part of this mission that can be completed without a physical device is complete and
verified: the quality layer exists, both adapters are wired to it, the stable profile is
proven identical to the verified baseline, the kill switch works, and 361 tests pass across
the audio and quality suites with a clean typecheck.

The verdict is PARTIAL and not PASS for one reason, stated plainly: **nobody has heard or
seen the result.** This environment has no physical device and no macOS build toolchain.
The mission's own quality targets — "crisp, clear, natural, low latency, visually sharp" —
are perceptual claims, and a perceptual claim verified only by unit tests is not verified.
Ten physical validation rows remain NOT PERFORMED and are listed in item 21.

Shipping state is safe: every flag is off, so the runtime configuration is byte-equivalent
to the baseline that was physically confirmed working. The elite work is built, tested, and
waiting behind a server-side switch that has not been thrown.

---

## 1. What was found before anything was changed

The quality settings for all eight surfaces lived in two object literals, one inside
`new Room({...})` in `useNativeCallRoom.ts` and one in `useLiveBroadcastRoom.ts`. They were
not identical and had no mechanism keeping them aligned. Changing either required an app
release. There was no way to change one for a QA cohort, and no way to change one back
without another release.

One real gap surfaced during measurement, and it is worth naming because it was invisible
from the code: **video calls passed no camera configuration at all.** The livestream path
carried explicit capture and publish options; the call path called
`setCameraEnabled(true)` bare, so resolution, frame rate, and bitrate were whatever LiveKit
chose that day. That is the largest single quality deficit found, and it was in the surface
least likely to be looked at.

## 2. The architecture that was built

Five modules, four new, all pure:

| Module | Role |
| --- | --- |
| `core/mediaQualityPolicy.ts` | Resolves feature + flags + conditions → a plan. Freezes the verified baseline as constants. Contains `buildRoomQualityOptions`, the single builder both adapters call. |
| `core/mediaAdaptationController.ts` | Pure reducer for network, thermal, battery, and device-tier adaptation. Holds the degradation ladder as data. |
| `core/mediaQualityFlags.ts` | Strict-boolean normalisation of the eight mission flags, camelCase and snake_case wire names. |
| `core/mediaQualityTelemetry.ts` | Fifteen event names over a closed field list. Rebuilds every field explicitly; never spreads input. |
| `core/realtimeAudioTelemetry.ts` (existing) | Reused for identifier hashing rather than duplicated. |

The load-bearing property is that **none of these can act**. No LiveKit import, no room
handle, no clock, no randomness, no audio API. They take values and return a plain object.
That is asserted by source-scanning tests against twelve forbidden markers, not left to
convention — because a policy module that can act is a second media owner, which is exactly
the failure the Audio Hard Lock exists to prevent.

## 3. Stable is provably the verified baseline

This is the mission's first non-negotiable and it is proven twice, by two independent
mechanisms, deliberately not sharing a source:

- `mediaQualityWiring.test.ts` **transcribes** both baseline Room-options literals by hand
  and asserts deep equality against `buildRoomQualityOptions(resolveMediaQualityPlan(...))`
  for all five features. It does not import the constants. If it did, changing a constant
  would move both sides of the assertion and the test would pass while the behaviour
  changed.
- `mediaQualityPolicy.test.ts` cross-checks the frozen constants against the adapter source
  **at the tagged commit `realtime-audio-stable-v1`**, read through `git show`. A constant
  cannot be quietly edited into agreement with a commit that is already history.

Additional assertions at stable: no key exists in the options object that was not in the
baseline; no `audioBitrate` is set, because the baseline set none; no
`degradationPreference` is set, for the same reason; and the result holds for `undefined`,
`null`, `{}`, `""`, `0`, `[]`, and `{ nonsense: true }` server payloads.

**PASS.**

## 4. The kill switch

`REALTIME_MEDIA_QUALITY_V2_ENABLED` is server-driven with no client override.
`normalizeMediaQualityFlag` accepts only a literal `true` — mirroring
`normalizeLiveAudioV2Flag`, which was already proven in production. Setting it false
restores the frozen configuration for every feature on the next room construction, with no
app release.

Tested with every other flag at elite: the kill switch alone returns the plan to stable
capture, stable publish, and stable video options. Narrower per-surface levers exist and
were tested independently.

**PASS.**

## 5. Elite audio

Five profiles resolve through one ladder: `speechResilient` 24k, `speechBalanced` 32k,
`speechElite` 40k, `musicBalanced` 64k, `musicElite` 96k.

The mission's instruction *"not permission to make voices sound robotic"* is enforced as a
test, not a preference: echo cancellation is `true` at every profile including music mode,
`red` (redundant encoding) stays on everywhere, and `stopMicTrackOnMute` stays false. Music
mode relaxes noise suppression, AGC, and DTX — which is the point of music mode — but
cannot disable echo cancellation.

Where the baseline left audio bitrate to the SDK, elite names it. That is the entire audio
change: a named bitrate and a content mode. No filter, no processing chain, no new capture
route.

**PASS** (implementation and policy). Perceptual confirmation is item 21.

## 6. Content mode does not oscillate

The mission required that *"automatic mode detection must not oscillate during a session"*.
It cannot: `resolveContentMode` maps `auto` → `speech` unconditionally and forces calls to
speech regardless of input. There is no runtime detector, so there is nothing to flap. Music
mode is an explicit choice, made once.

This is a deliberate under-delivery. A detector that switches mid-session is worse than no
detector, and the mission said so.

**PASS.**

## 7. Elite video and the camera profiles

Three tiers: elite 1080×1920 @30 / 4 Mbps, balanced 720×1280 @30 / 2.3 Mbps, resilient
480×854 @24 / 900 kbps. Guests are capped at 60 % of the host tier. All non-stable video
carries `degradationPreference: "maintain-framerate"`, which encodes the mission's ordering
preference at the SDK level.

The video-call gap is closed: at elite, video calls receive capture and publish
configuration for the first time. At stable they still receive none, because that is what
the baseline did.

**PASS** (implementation). Sharpness is perceptual — item 21.

## 8. Digital zoom cannot return

The mission was specific: *"The previous host-camera full-zoom behavior must never
return."* This is guarded at the type level rather than by review. A capture object can
carry only `facingMode`, `frameRate`, and `resolution`, asserted by `Object.keys().sort()`
across every profile and feature. There is no field a zoom factor could occupy, and no code
path that computes one.

**PASS.**

## 9. The degradation order

The mission's order is stored as data so a test can assert it verbatim rather than a
reviewer trusting that code implements a document:

```
full_quality → reduce_layers → reduce_bitrate → reduce_resolution
→ reduce_frame_rate → pause_remote_video
```

Frame rate is reduced last before the last resort, exactly as specified. Effects are
cumulative: by the resolution rung, layers and bitrate are already reduced.

**PASS.**

## 10. Audio is not on the ladder

`AUDIO_DEGRADATION_RUNGS` is the empty array. Every adaptation decision carries
`audioPreserved: true`; every rung's effects carry `audioUnchanged: true`. These fields
exist so that tests can assert a positive rather than infer from an absence — an absence
proves nothing about the next rung someone adds.

Asserted across every rung, every thermal state, every network tier, and every battery
level.

**PASS.**

## 11. Adaptation cannot oscillate

Hysteresis is asymmetric by design: two consecutive samples to descend, five to ascend; a
4-second minimum between changes and a 15-second dwell before any upgrade; at most one rung
up per decision, two down. Degrade fast, recover slowly.

Verified by driving the reducer, not by reading it: a 100-sample flapping network produces
at most 2 rung changes; a 200-sample noisy sequence produces at most 12.

**PASS.**

## 12. Adaptation never reconnects the room

The mission forbade reconnecting to adjust quality. The controller is a pure reducer — it
has no room handle and no network access, so it could not reconnect if it wanted to. It
returns a rung; something else would have to act on it.

**PASS**, structurally.

## 13. Thermal, battery, and device tier

Guards are a one-way clamp, verified by an exhaustive 4×3×3×4 sweep over thermal state,
network tier, device tier, and battery condition: conditions can only lower the profile,
never raise it. Serious thermal state clamps to resilient. Battery ≤15 % uncharged clamps
to resilient, ≤30 % to balanced. A high-tier device on a good network at full battery is
the only combination that leaves elite untouched.

The mission's *"do not hard-code maximum settings for every device and network"* is
satisfied by construction: the clamp runs before the plan is built, on every resolve.

**PASS.**

## 14. Viewers still cannot publish

At stable and at elite, a viewer receives no `videoCaptureDefaults` and no
`videoEncoding` — nothing to publish with. The quality plan never sees a token and cannot
grant a right. Guest publication remains gated by `canConnectAsCohostPublisher`, untouched.

**PASS.**

## 15. Participant permissions are untouchable

Neither adapter contains `setPermissions` or `updateParticipant(`, asserted as an absence
in both files. The quality layer has no path to either.

**PASS.**

## 16. No second microphone route, no second publication path

Asserted directly in both adapters: no `createLocalAudioTrack`, no `getUserMedia`, no
`mediaDevices.getUserMedia`. The microphone is still published through
`publishRealtimeMicrophone` (calls) and `publishLiveMicrophone` (live) — one publisher
each, unchanged.

`registerGlobals({ autoConfigureAudioSession: false })` is still present in both adapters,
and the audio lease is still acquired **before** the Room is constructed — asserted by
source index ordering, so a reordering fails the test rather than passing review.

**PASS.**

## 17. Audio before camera, still

`initializeCallLocalMedia` still returns early on `audioTrackCount <= 0` before it reaches
any camera call. The camera branch added for elite sits after that guard. Asserted by index
ordering inside the function body, with comments stripped — the guarantee is about what the
code does, not what its comments discuss.

**PASS.**

## 18. Telemetry carries no content and no credentials

Fifteen event names over a closed field list. `emitMediaQualityEvent` never spreads its
input; it rebuilds every field explicitly, so a field added upstream cannot leak through.
Identifiers are hashed by the existing `hashRealtimeAudioIdentifier`. Reason codes must
match `/^[a-z0-9_]{1,48}$/` and are **dropped** rather than redacted when they do not — a
redacted string still reveals that something was there and how long it was.

No raw audio, no raw video, no token, no credential can pass through it.

**PASS.**

## 19. Regression protection

The four new modules are now a protected category (`media_quality_policy`) in
`config/realtime-audio-protected-paths.json`. The three new test suites are in
`critical_audio_tests`. Both were added to `npm run test:realtime-audio-critical` and
`npm run test:realtime-audio`.

The import boundary was tightened: the quality modules join the audio core in
`import_boundary.modules`, so they are reachable only from the two adapters that hold the
audio lease. A screen that resolves its own plan now fails CI. This matters more than it
looks — a screen resolving its own plan is a screen producing a second set of Room options
that nobody knows exists.

The change gate correctly identified all 14 protected paths and demanded a declaration.

**PASS.**

## 20. Test results — measured, not claimed

| Check | Command | Result |
| --- | --- | --- |
| Critical audio suite | `npm run test:realtime-audio-critical` | **PASS** — 13 suites, 283 tests, 5.4 s |
| Full audio suite | `npm run test:realtime-audio` | **PASS** — 20 suites, 361 tests, 6.1 s |
| Quality policy / adaptation / wiring | `jest src/core/__tests__/mediaQuality src/core/__tests__/mediaAdaptation` | **PASS** — 3 suites, 134 tests |
| Architecture (native) | `npm run test:realtime-audio-architecture` | **PASS** — 21 tests |
| Architecture (backend) | `python3 -m unittest tests.protection.test_realtime_audio_architecture` | **PASS** — 13 tests, `OK` |
| Backend token grants | 3 protection modules | **PASS** — 4 tests, `OK` |
| TypeScript | `npm run typecheck` | **PASS** — no errors |
| Change gate | `scripts/realtime_audio_change_gate.py` | **PASS** — 14 protected paths, declaration accepted |
| Native build | `expo prebuild` / EAS | **NOT RUN** — no macOS toolchain here |

## 21. What has not been validated

This is the honest core of the report.

| Validation | Status |
| --- | --- |
| Audio call, both directions audible | NOT PERFORMED |
| Video call, both directions audible with video active | NOT PERFORMED |
| Livestream viewer hears host | NOT PERFORMED |
| Livestream host hears approved guest | NOT PERFORMED |
| Route change (speaker / receiver / Bluetooth) | NOT PERFORMED |
| Interruption recovery (PSTN call, then resume) | NOT PERFORMED |
| Mixed session without app restart | NOT PERFORMED |
| A/B stable vs elite — audio clarity, no robotic artefacts | NOT PERFORMED |
| A/B stable vs elite — camera sharpness, no forced zoom | NOT PERFORMED |
| Elite live host at 1080p, 10 min sustained — thermal and battery | NOT PERFORMED |
| Degradation under a throttled network — audio must stay continuous | NOT PERFORMED |
| Recovery after network returns — no oscillation | NOT PERFORMED |
| Native iOS build | NOT RUN — no macOS/Xcode toolchain |

The first seven rows are required **before this change ships at all**, because it edits both
room adapters even though it changes no behaviour. The remaining rows are required **before
any flag is enabled**, including for the QA cohort.

The residual risk no unit test can retire: at elite, a live host encodes 1080×1920 at
4 Mbps on real hardware in a real room. Thermal behaviour, battery drain, and whether the
encoder actually sustains 30 fps are physical facts. The thermal clamp to resilient at
`serious` is the designed mitigation; only a device can confirm it fires in time.

## 22. Rollout position and what to do next

Shipping state: all eight flags off, `REALTIME_MEDIA_QUALITY_V2_QA_ONLY` true. Runtime
configuration is byte-equivalent to the physically verified baseline.

Recommended order, and the mission's own implementation order picks up here:

1. Perform the seven physical audio rows against this build with flags off. This confirms
   the "no behaviour change" claim that everything else rests on.
2. Run a native iOS build in CI (`native-build` job) — required and not possible here.
3. Enable `REALTIME_MEDIA_QUALITY_V2_ENABLED` with `QA_ONLY` true for the QA cohort only.
   Enable one surface at a time: call audio first (smallest change), then live audio, then
   video-call quality, then live video last (largest change).
4. Run the A/B and thermal rows at each step. Roll back the single surface flag, not the
   master switch, if a step fails — that keeps the evidence about which surface broke.
5. Only after all rows pass on a device: widen the cohort gradually.

Do not enable live elite video before the 10-minute thermal row has been run. It is the
one change in this mission with a plausible path to making a device unusable mid-broadcast,
and it is the last one in the sequence for that reason.

---

## Appendix — governance artefacts

| Artefact | Path |
| --- | --- |
| Change declaration (this change) | `reports/realtime_audio_change_declaration.md` |
| Declaration archive (Mission D) | `reports/realtime_audio_change_declaration_history.md` |
| Protected-path manifest | `config/realtime-audio-protected-paths.json` |
| Change gate | `scripts/realtime_audio_change_gate.py` |
| Verified baseline of record | `reports/realtime_audio_verified_baseline.md` |
| Release checklist | `docs/realtime_audio_release_checklist.md` |
