# Real-Time Audio Change Declaration

**Change:** Mission E — PulseSoc elite live media quality, governed quality policy layer
**Base commit:** `985c3886944fdcb343dc3a84915d139dad553cbf`
**Baseline of record:** `reports/realtime_audio_verified_baseline.md`, tag `realtime-audio-stable-v1`
**Label required:** `audio-critical-change`
**Declared on:** 2026-08-02

> The previous declaration (Mission D, the hard-lock itself) is preserved in full at
> `reports/realtime_audio_change_declaration_history.md`.

## Why the change is required

Mission E's objective is measurably better audio and video across eight surfaces. Every
setting that governs that quality — capture constraints, publish defaults, encoding,
simulcast — was written as an object literal inside `new Room({...})` in the two room
adapters. Two literals, drifting independently, with no way to change either one without
an app release and no way to change one for a QA cohort only.

So the quality settings had to move out of the adapters and behind a server-driven flag.
That is the change. It cannot be made from outside the adapters, because the adapters are
where the literals were.

The mission's own non-negotiable condition set the shape of the solution: *"Do not alter
the proven audio ownership, publication, subscription, routing, or cleanup architecture."*
The literals moved; nothing around them did. The lease is still acquired before the Room
is constructed, the microphone is still published by the one publisher, and with every
flag off the object handed to `new Room(...)` is byte-equivalent to the literal that was
there before.

Two things this change is deliberately **not**: it is not a filter layer, and it is not a
new media path. The four new modules cannot act. They are pure functions that return a
plain object; they hold no room handle, import no LiveKit SDK, read no clock, and call no
audio API. That is enforced by source-scanning tests, not by intention.

## Which feature required it

**Mission E — PulseSoc elite live media quality.** Its subject is real-time media, so
`docs/realtime_audio_change_policy.md`'s unrelated-mission rule does not apply. The
ordinary requirements do: this declaration, the `audio-critical-change` label, full
validation, and physical re-validation before any rollout past the QA cohort.

Concurrent unrelated work (Commerce Inbox, Orders, Business OS) was present in the working
tree and is **excluded** from this change set by explicit file staging. No protected path
in this declaration was touched by that work.

## Which protected files changed

Output of `python3 scripts/realtime_audio_change_gate.py --changed-files-from ...` against
this change set — 14 protected files:

| File | Manifest category | What changed |
| --- | --- | --- |
| `mobile-native/src/core/mediaQualityPolicy.ts` | `media_quality_policy` | **New module.** The resolver. Freezes the verified baseline as `BASELINE_AUDIO_CAPTURE`, `BASELINE_AUDIO_PUBLISH`, `BASELINE_LIVE_VIDEO_CAPTURE`, `BASELINE_LIVE_VIDEO_PUBLISH`, and returns exactly those for every feature when V2 is off. Four profiles (`stable`, `balanced`, `elite`, `resilient`), five features, a one-way condition clamp, and `buildRoomQualityOptions` — the single builder both adapters now call. |
| `mobile-native/src/core/mediaAdaptationController.ts` | `media_quality_policy` | **New module.** A pure reducer for network/thermal/battery adaptation. The mission's degradation order is stored as data (`DEGRADATION_LADDER`) so a test can assert it verbatim. `AUDIO_DEGRADATION_RUNGS` is the empty array and every decision carries `audioPreserved: true`: audio is not on the ladder at all, and that is checkable rather than inferable. Asymmetric hysteresis (2 samples down, 5 up; 4 s cooldown, 15 s upgrade dwell) prevents oscillation. |
| `mobile-native/src/core/mediaQualityFlags.ts` | `media_quality_policy` | **New module.** Strict-boolean normalisation of the eight mission flags, mirroring `normalizeLiveAudioV2Flag`: only a literal server `true` enables anything. Accepts both camelCase and the snake_case wire names. Defaults are all-off with `realtimeMediaQualityV2QaOnly: true`, so an older backend that omits the field runs stable. |
| `mobile-native/src/core/mediaQualityTelemetry.ts` | `media_quality_policy` | **New module.** Fifteen event names over a closed field list — enums, bitrates, resolutions, frame rates, loss/RTT/jitter, hashed identifiers. It never spreads its input; every field is rebuilt explicitly. Reason codes must match `/^[a-z0-9_]{1,48}$/` and are **dropped** rather than redacted if they do not. No raw audio, video, token, or credential can pass through it. |
| `mobile-native/src/calls/useNativeCallRoom.ts` | `audio_and_video_call_adapter` | The 15-line `new livekitClient.Room({...})` literal was replaced by `resolveMediaQualityPlan(...)` → one telemetry event → `new livekitClient.Room(buildRoomQualityOptions(plan))`. `initializeCallLocalMedia` gained an optional `quality` plan and branches: with capture+publish options it calls `setCameraEnabled(true, capture, publish)`, otherwise the unchanged bare `setCameraEnabled(true)`. At stable the branch is never taken. No lease, publication, ordering, or cleanup code changed. |
| `mobile-native/src/live/useLiveBroadcastRoom.ts` | `livestream_audio_adapter` | Same substitution at the Room construction site. `enableCamera` and the exported `setCameraEnabled` now prefer the plan's options and fall back to the existing `PULSE_LIVE_VIDEO_*` constants. `liveAudioMode`'s declared return type was narrowed to `Extract<RealtimeAudioMode, "live_host" \| "live_guest" \| "live_viewer">` — the narrower type is what it always returned, and naming it lets one value drive both the audio lease and the quality policy, so the two cannot disagree about what this participant is. |
| `mobile-native/src/live/liveSession.ts` | `livestream_audio_adapter` | Added `mediaQuality?: Record<string, unknown> \| null` to `LiveKitCredentials` and one normalisation branch in `normalizeLiveKitCredentials` that accepts a plain object and yields `null` otherwise. The raw payload is carried, not interpreted — flag semantics stay in one module. |
| `mobile-native/src/api/calls.ts` | `backend_token_and_room_policy` | One optional field on `PulseCallJoin`: `media_quality?: Record<string, unknown>`. No token, grant, or room-policy change. |
| `mobile-native/src/core/__tests__/mediaQualityPolicy.test.ts` | `critical_audio_tests` | **New file.** Nine gate groups. Gate 1 is load-bearing: it now cross-checks the frozen constants against the adapter sources **at the tagged commit `realtime-audio-stable-v1`**, because wiring deliberately removed those literals from the working tree. A constant cannot be edited into agreement with a commit that is already history. If the tag is unreachable (shallow clone) the cross-check warns rather than passing silently. |
| `mobile-native/src/core/__tests__/mediaAdaptationController.test.ts` | `critical_audio_tests` | **New file.** Eight gate groups, including a 100-sample flapping network that must produce at most 2 rung changes, and a 200-sample noisy sequence capped at 12. |
| `mobile-native/src/core/__tests__/mediaQualityWiring.test.ts` | `critical_audio_tests` | **New file.** The independent witness. It transcribes both baseline Room-options literals rather than importing the constants, precisely so a change to a constant cannot move both sides of the assertion. It also asserts the adapters contain no second hand-written `new livekitClient.Room({` literal, resolve the plan exactly once each, still acquire the lease before constructing the Room, and never reference `createLocalAudioTrack`, `getUserMedia`, `setPermissions`, or `updateParticipant(`. |
| `mobile-native/src/live/__tests__/liveSession.test.ts` | `critical_audio_tests` | One field added to an exhaustive `toEqual`: `mediaQuality: null`. The assertion stays exhaustive on purpose — a new field appearing on the credentials object should require someone to say so. |
| `config/realtime-audio-protected-paths.json` | `audio_governance` | New `media_quality_policy` category covering the four modules; three new test files added to `critical_audio_tests`; the four modules added to `import_boundary.modules` with their own files added to `allowed_importers`. That last part is the tightening: the quality resolver is now reachable only from the two adapters that hold the audio lease, so no screen can resolve its own plan and produce a second set of Room options. |
| `mobile-native/package.json` | `dependency_watch` | Script-only. `test:realtime-audio-critical` and `test:realtime-audio` now include the three new suites. **No dependency version changed** — the pinned media stack is byte-identical to the baseline and the dependency-lock test asserts equality against it. |

Unprotected files also changed (listed for completeness, no declaration consequence):
`reports/realtime_audio_change_declaration_history.md` (new), this file, and
`reports/pulsesoc_elite_media_quality_report.md` (new).

## Expected behavior change

**None, with every flag off — which is the shipped state.**

`REALTIME_MEDIA_QUALITY_V2_ENABLED` defaults false and is server-driven with no client
override, so a build with no backend change behaves exactly as the verified baseline did.
Stated precisely, because "none expected" is the claim the physical validation tests:

- The object passed to `new Room(...)` is deep-equal to the previous literal, for all
  five features. Asserted against independently transcribed literals in
  `mediaQualityWiring.test.ts`, and against the tagged baseline commit in
  `mediaQualityPolicy.test.ts`.
- A stable video call still calls `setCameraEnabled(true)` with no arguments, because
  `videoCaptureFor("stable", "video_call")` returns `undefined` on purpose. The baseline
  passed no camera options for video calls; returning the livestream's options here would
  be a behaviour change wearing the word "stable".
- No `AVAudioSession` call was added, removed, or reordered. No screen-level audio-session
  mutation exists.
- The audio lease is still acquired before the Room is constructed (asserted by index
  ordering in the wiring test), and released by lease, not by owner name.
- No microphone track is created, published, unpublished, muted, or unmuted at a different
  time. There is no second publication path.
- No remote subscription, route selection, reconnect, or cleanup logic changed.
- No participant permission is touched. The quality layer cannot call `setPermissions` or
  `updateParticipant` — asserted as an absence in both adapters.
- A viewer still gets no publish configuration at any profile, including elite.
- No dependency version changed.

The only observable difference with flags off is one additional telemetry event per room
construction (`quality_plan_resolved`), carrying enums and hashed identifiers.

**With flags on (QA cohort only, not shipped):** audio gains a named bitrate where the
baseline left it to the SDK (40 kbps speech at elite); the live host rises to 1080×1920
with `degradationPreference: "maintain-framerate"`; video calls receive capture and publish
configuration they never had. Echo cancellation is never disabled at any profile, and
music mode — which relaxes noise suppression and AGC — still leaves echo cancellation on.

## Regression risk

| Verified surface | Can this change affect it? | Why |
| --- | --- | --- |
| Audio call | No, with flags off | The Room options are deep-equal to the previous literal, proven against a commit and against an independent transcription. Nothing else in the adapter moved. |
| Video-call audio | No, with flags off | Same construction site, same options. The camera branch that could differ is not taken at stable, and the bare `setCameraEnabled(true)` call is asserted to still exist. |
| Livestream host audio | No, with flags off | Same substitution. `PULSE_LIVE_VIDEO_*` remain the fallback when no plan options are present. |
| Livestream guest audio | No, with flags off | Guest publication is gated by `canConnectAsCohostPublisher`, which is untouched. The quality plan cannot grant publish rights — it never sees a token. |
| Livestream viewer playback | No | Viewers publish nothing at any profile; asserted at stable and at elite. `mediaQuality` normalising to `null` on an older backend yields stable. |
| Routing (speaker / receiver / Bluetooth) | No | No routing call added or moved. |
| Interruption recovery | No | No interruption handler modified. |
| Cleanup | No | Lease discipline unchanged; the manifest's `required_lease_discipline` check passes on both adapters. |
| Mixed-session transitions | No | Ownership arbitration untouched. `liveAudioMode` now drives both the lease and the plan, which removes a way for them to disagree rather than adding one. |

Residual risk automation cannot retire: with flags **on**, elite raises the live host to
1080p and gives video calls encoder settings they never had. That is a real encoder and
thermal change on real hardware, and no unit test can tell you how a phone behaves at
1080p in a warm room. It is why the flags ship off, why `REALTIME_MEDIA_QUALITY_V2_QA_ONLY`
defaults true, and why the adaptation reducer clamps to `resilient` on `serious` thermal
state before anything else is considered. The physical A/B below is what actually retires
this risk.

## Tests run

Measured 2026-08-02 on this change set, not claimed.

| Check | Command | Result |
| --- | --- | --- |
| Critical audio suite | `npm run test:realtime-audio-critical` | **PASS** — 13 suites, 283 tests, 5.4 s |
| Full audio suite | `npm run test:realtime-audio` | **PASS** — 20 suites, 361 tests, 6.1 s |
| Architecture (native) | `npm run test:realtime-audio-architecture` | **PASS** — 21 tests |
| Architecture (backend) | `python3 -m unittest tests.protection.test_realtime_audio_architecture` | **PASS** — 13 tests, `OK` |
| Backend token grants | `python3 -m unittest tests.protection.test_call_livekit_token_grants tests.protection.test_livestream_audio_token_grants tests.protection.test_livekit_webhook_route_owner` | **PASS** — 4 tests, `OK` |
| Quality policy + adaptation + wiring | `jest src/core/__tests__/mediaQuality src/core/__tests__/mediaAdaptation` | **PASS** — 3 suites, 134 tests |
| TypeScript | `npm run typecheck` | **PASS** — no errors |
| Change gate | `python3 scripts/realtime_audio_change_gate.py --changed-files-from ...` | Correctly identified all 14 protected paths and demanded this declaration |
| Native build | `npx expo prebuild --platform ios --no-install` or an EAS build | **NOT RUN** — no macOS/Xcode toolchain in this environment. Required before release; the workflow's `native-build` job runs it in CI. |

## Physical validation required

Automated tests prove the resolver returns the baseline and the adapters use it. They
cannot prove a human heard anything. This change edits both room adapters, so every audio
row is required before it reaches production even with flags off.

Record results in `reports/realtime_audio_verified_baseline.md` section 7, not here.

| Surface | Required? | Performed? | Device / iOS | Result |
| --- | --- | --- | --- | --- |
| Audio call, both directions audible | Required | NOT PERFORMED | | |
| Video call, both directions audible with video active | Required | NOT PERFORMED | | |
| Livestream viewer hears host | Required | NOT PERFORMED | | |
| Livestream host hears approved guest | Required | NOT PERFORMED | | |
| Route change (speaker / receiver / Bluetooth) | Required | NOT PERFORMED | | |
| Interruption recovery (incoming PSTN call, then resume) | Required | NOT PERFORMED | | |
| Mixed session without app restart | Required | NOT PERFORMED | | |

Additionally required **before any flag is enabled for the QA cohort**, and not satisfied
by the rows above:

| Quality check | Required? | Performed? | Result |
| --- | --- | --- | --- |
| A/B stable vs elite, audio call — clarity, no robotic artefacts | Required | NOT PERFORMED | |
| A/B stable vs elite, live host camera — sharpness, no forced zoom | Required | NOT PERFORMED | |
| Elite live host at 1080p, sustained 10 min — thermal and battery | Required | NOT PERFORMED | |
| Degradation under a throttled network — audio must stay continuous | Required | NOT PERFORMED | |
| Recovery after the network returns — no oscillation | Required | NOT PERFORMED | |

**NOT PERFORMED** is recorded honestly rather than left blank. This environment has no
physical device and no macOS build toolchain. The release checklist blocks on these rows,
and its rule stands: the only acceptable positive result is that a person heard the audio
and saw the video.

## Rollback procedure

- **Immediate mitigation (no app release):** set `REALTIME_MEDIA_QUALITY_V2_ENABLED=0`
  server-side. `normalizeMediaQualityFlag` requires a literal `true`, so this restores
  the frozen baseline configuration for every feature on the next room construction. This
  is a real kill switch, not a code path that hopes to be equivalent — the same builder
  runs, with the stable plan, producing the object the baseline used. It requires no
  client change and takes effect for new sessions immediately. Narrower levers exist per
  surface: `LIVE_ELITE_AUDIO_ENABLED`, `LIVE_ELITE_VIDEO_ENABLED`,
  `CALL_ELITE_AUDIO_ENABLED`, `VIDEO_CALL_ELITE_QUALITY_ENABLED`.
- **Code rollback:** `git revert <sha>` of this commit. Safe to revert whole: the four
  new modules have no importers outside the two adapters, and reverting restores both
  literals together. Alternatively `git checkout realtime-audio-stable-v1`, the immutable
  snapshot of the physically-validated foundation.
- **Backend rollback:** remove `media_quality` from the call-join and LiveKit-token
  responses. The client treats an absent or malformed payload as stable — asserted for
  `undefined`, `null`, `{}`, `""`, `0`, `[]`, and `{ nonsense: true }` — so an older
  backend needs no client change.
- **Who to notify:** the real-time audio owner in `.github/CODEOWNERS` (`@hmcroody-alt`),
  plus whoever is on release duty for the affected build.
- **How to confirm the rollback worked:** a person places an audio call and hears both
  directions; a person joins a livestream and hears the host. Nothing less counts. A green
  test run after a rollback confirms only that the code compiles and the invariants are
  stated — it is not evidence of sound.
---

## Template for the next change

Copy the block below over everything above it, then fill it in. The gate checks that
each heading is present **by name** and that every changed protected file is named
somewhere in the file. Paste the file list from:

```
python3 scripts/realtime_audio_change_gate.py --base <base> --head HEAD
```

```markdown
# Real-Time Audio Change Declaration

## Why the change is required
<!-- What breaks, or cannot be built, without touching a protected path? If the
     honest answer is "it was convenient", the change belongs outside the boundary. -->

## Which feature required it
<!-- Name the mission or ticket. If this is an unrelated mission (Marketplace,
     Advertising, Premium, Crypto, Profile, Feed, Settings, Search, general UI),
     docs/realtime_audio_change_policy.md requires owner approval on top of
     everything below. -->

## Which protected files changed
| File | Manifest category | What changed |
| --- | --- | --- |
|  |  |  |

## Expected behavior change
<!-- State it in terms of what a person on a phone would notice. "None" is a valid
     and common answer — but say it explicitly, because "none expected" is the claim
     the physical validation is testing. -->

## Regression risk
<!-- Which verified surfaces could this plausibly break: audio call, video-call
     audio, livestream host, livestream guest, livestream viewer playback, routing,
     interruption recovery, cleanup, mixed-session transitions. For each, say why it
     can or cannot be affected. -->

## Tests run
<!-- Paste the actual result lines, not a claim that they passed. -->
| Check | Command | Result |
| --- | --- | --- |
| Critical audio suite | `npm run test:realtime-audio-critical` |  |
| Full audio suite | `npm run test:realtime-audio` |  |
| Architecture (native) | `npm run test:realtime-audio-architecture` |  |
| Architecture (backend) | `python3 -m unittest tests.protection.test_realtime_audio_architecture` |  |
| Backend token grants | `python3 -m unittest tests.protection.test_call_livekit_token_grants tests.protection.test_livestream_audio_token_grants` |  |
| TypeScript | `npm run typecheck` |  |
| Native build | `npx expo prebuild --platform ios --no-install` or an EAS build |  |

## Physical validation required
<!-- Mark rows this change cannot affect as "not required" with a one-line reason.
     Delete no rows. Record results in reports/realtime_audio_verified_baseline.md. -->
| Surface | Required? | Performed? | Device / iOS | Result |
| --- | --- | --- | --- | --- |
| Audio call, both directions audible |  |  |  |  |
| Video call, both directions audible with video active |  |  |  |  |
| Livestream viewer hears host |  |  |  |  |
| Livestream host hears approved guest |  |  |  |  |
| Route change (speaker / receiver / Bluetooth) |  |  |  |  |
| Interruption recovery (incoming PSTN call, then resume) |  |  |  |  |
| Mixed session without app restart |  |  |  |  |

## Rollback procedure
<!-- Concrete steps, not "revert the commit". -->
- Immediate mitigation (no app release):
- Code rollback: `git revert <sha>` / `git checkout realtime-audio-stable-v1`
- Backend rollback:
- Who to notify:
- How to confirm the rollback worked (what must be heard):
```
