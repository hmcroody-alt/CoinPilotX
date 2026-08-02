# PulseSoc Real-Time Audio Hard-Lock — Consolidated Report

**Mission:** D — enforceable repository, CI, testing, ownership, and runtime safeguards around the verified working real-time audio foundation
**Date:** 2026-08-02
**Repository:** `CoinPilotX` (`git@github.com:hmcroody-alt/CoinPilotX.git`)
**Branch:** `codex/store-dashboard-live`
**Baseline commit:** `ce03e160eaf4649a8e02bc3b609a3182ca9d3859` (HEAD == `origin/codex/store-dashboard-live`)
**Snapshot tag:** `realtime-audio-stable-v1` → `ce03e160` (created locally, **not yet pushed**)

---

## Verdict

# PARTIAL

Nineteen of the twenty required sections are complete and verified by measurement.
The lock fails to reach PASS on exactly one axis, and it is not a code axis: **branch
protection and the PR label automation are GitHub server-side settings that cannot be
configured from this environment**, and the mission states plainly, *"Do not claim
branch protection is active unless it was actually configured and verified."* They are
specified in full and left honestly marked NOT CONFIGURED rather than asserted.

A second, smaller gap: **physical audible re-validation of this change has not been
performed**, because there is no device and no macOS build toolchain here. The change
is telemetry-only and expected to be inaudible, but "expected to be inaudible" is
precisely the claim the mission requires a human ear to settle.

Both gaps are recorded, both have written procedures, and neither is silent. Nothing
in this repository claims a protection is active when it is not.

---

## 1. Verified baseline document

**PASS.** `reports/realtime_audio_verified_baseline.md`, eleven sections.

Every claim is labelled **repository-evidenced** or **owner-attested**, and anything
unknowable from here is written **NOT RECORDED** rather than filled with a plausible
value. Commit identity is exact: branch `codex/store-dashboard-live`, HEAD and remote
both `ce03e160eaf4649a8e02bc3b609a3182ca9d3859`, last audio-touching commit
`b252a255e675c1b3e065e602ef225adc3c31779a`, with a demonstration that the audio
implementation at `ce03e160` is byte-identical to `b252a255` — so the attestation
transfers to HEAD legitimately rather than by assumption.

Section 7 records the attested results without softening them: audio call PASS, video
call PASS, livestream viewer playback PASS, **livestream guest NOT SEPARATELY
ATTESTED**, **mixed sessions PARTIALLY ATTESTED**. Six explicit non-claims follow, so
a future reader cannot mistake the document's silence for coverage. No vague term —
"connected", "appeared functional" — appears anywhere in it.

**Gap:** device model, iOS version, build number, and test date are NOT RECORDED. They
were not supplied and were not invented.

## 2. Machine-readable protected-path manifest

**PASS.** `config/realtime-audio-protected-paths.json`, `manifest_version: 1`.

It is used, not merely documented. Three independent consumers read this one file:
`mobile-native/src/core/__tests__/realtimeAudioArchitecture.test.ts` (Jest),
`tests/protection/test_realtime_audio_architecture.py` (Python), and
`scripts/realtime_audio_change_gate.py` (the CI gate). There is exactly one copy of
the rules, so what CI enforces cannot drift from what is written down — the failure
mode of every hand-maintained second copy.

Fourteen categories, seven `backend_diff_patterns`, six `forbidden_apis`, the
`import_boundary`, `required_lease_discipline`, `dependency_watch`, `test_commands`,
`declaration`, and `unrelated_mission_policy`. Manifest integrity is itself tested:
every protected path must point at a file that exists, because an entry naming a
deleted file is worse than no entry — the gate silently stops protecting whatever
replaced it.

## 3. Architecture tests blocking direct bypasses

**PASS.** Two enforcement points, 21 Jest tests and 13 Python tests, all passing.

Six forbidden-API rules, each scanning every non-test `.ts`/`.tsx` under
`mobile-native/src`:

| Rule | Confined to |
| --- | --- |
| `realtime_audio_session_mutation` | the engine only |
| `unmanaged_microphone_publication` | engine + publisher |
| `direct_remote_audio_subscription` | the engine only |
| `global_livekit_audio_manager_mutation` | engine + the two room adapters |
| `expo_av_global_audio_mode` | a frozen six-file legacy allowlist |
| `direct_realtime_cleanup` | the engine only |

**No wildcard covers a directory.** This is asserted, not merely intended: both readers
fail if any allowlist entry contains `*`, if it does not end in a source extension, or
if it names a file that does not exist. A directory allowlist would let a new file
dropped into `core/` bypass the whole boundary on the day it is created.

The `expo_av_global_audio_mode` allowlist carries `frozen_at_baseline: true` and
`max_allowed_paths: 6`, and a test fails if a seventh entry appears. Those six files
already mutated the global audio mode at the verified baseline; they are frozen rather
than rewritten because this hard-lock must not change working runtime behavior.

One hazard worth recording: a bare `setCategory|setMode|setActive` grep matches React
`setState` helpers across dozens of unrelated screens. Every marker is audio-qualified
for that reason, which is why the scan produces zero false positives across the tree.

## 4. Narrow public API

**PASS**, by measurement rather than by construction.

Measurement showed the surface was already narrow: only `useNativeCallRoom.ts`,
`useLiveBroadcastRoom.ts`, and `liveAudioPublisher.ts` import the audio core, and **no
screen does**. Rather than delete exports that tests legitimately use — which would
weaken the tests to produce a cosmetic improvement — the existing narrowness is now
*enforced* as an `import_boundary` rule in both readers: the four core modules are
importable only from eight named files.

The rule matches the module's last path segment inside any relative specifier, so
moving an importing file one directory deeper does not evade it. A companion test
asserts every approved importer is a real file, because a stale entry is a hole: the
next file created at that path inherits an exemption nobody granted.

One genuinely dead export was removed: `PULSE_LIVE_PORTRAIT_VIDEO_RESOLUTION`, which
had zero importers and is still consumed internally.

## 5. Contract tests for every verified invariant

**PASS.** 42 tests in `realtimeAudioContracts.test.ts`, covering ownership arbitration
(priorities `audio_call`/`video_call` 100, `voice_message` 90, `live_host`/`live_guest`
80, `live_viewer` 40, `music_playback` 10; outcomes `granted | reacquired | displaced |
denied`), generation-scoped lease behavior, serialized publication outcomes
(`already_published | published | timeout | no_participant | forbidden`), and **all
eight mixed-session transitions**.

The lease-discipline rule is separately enforced against both room adapters: they must
release audio by lease, and `audioOwnerIdRef` — the pre-baseline owner-name pattern —
must not reappear. Its return would mean a delayed cleanup can once again release a
session a newer feature has since acquired, which is the exact bug the lease
generation fixed.

`registerGlobals` is allowed in the two adapters, but a test checks the *argument*:
every call must contain `autoConfigureAudioSession: false`. Allowing the call without
checking its argument would permit the one variant that breaks everything.

## 6. Golden-flow critical suite

**PASS**, and measurably faster than the full suite.

| Suite | Command | Result | Time |
| --- | --- | --- | --- |
| Critical | `npm run test:realtime-audio-critical` | 10 suites, **149 tests**, PASS | 4.6 s |
| Full | `npm run test:realtime-audio` | 17 suites, **227 tests**, PASS | 5.5 s |

The critical suite pins its members by explicit path (`--runTestsByPath`), so a
renamed or relocated test surfaces as a hard error rather than silently dropping out
of the gate. A test that quietly stops running is indistinguishable from a test that
passes.

## 7. Change-detection CI gate requiring a written declaration

**PASS**, and demonstrated in both directions.

`scripts/realtime_audio_change_gate.py` maps changed paths to manifest categories,
applies content-based matching to `bot.py` via `backend_diff_patterns`, and rejects a
declaration that is unfilled or that fails to name every protected file changed.

Proven by execution:

| Case | Result |
| --- | --- |
| Protected path changed, template declaration | `EXIT=1` — "is still the unfilled template" **and** "does not name these changed protected files: …" |
| Unprotected paths only (`MarketplaceScreen.tsx` + non-matching `bot.py` diff) | `EXIT=0` — "No protected real-time audio path changed (2 file(s) inspected)" |
| This mission's own 13 protected files, declaration filled | `EXIT=0` — "Declaration accepted" |

`bot.py` is protected by diff *content* rather than by path deliberately. Path-
protecting one enormous module would force an audio declaration on every backend
change, which trains people to write the declaration without reading it — the failure
mode section 20 warns about.

`reports/realtime_audio_change_declaration.md` is now a filled instance covering this
mission's own change, with the blank template preserved at the bottom for the next
author. A gate whose own introducing commit bypasses it is a gate nobody believes in
afterwards.

## 8. Dependency lock

**PASS.** `dependency_watch.baseline_versions` records the exact strings, and both
readers assert **equality**, not range satisfaction:

`@livekit/react-native ^2.9.0`, `@livekit/react-native-webrtc 144.1.1`,
`livekit-client ^2.15.4`, `expo-av ~16.0.8`, `expo ~54.0.36`, `react-native ^0.81.5`.

Equality is the point: the media stack cannot move without someone editing the
manifest, and editing the manifest is itself a protected change that the gate then
requires a declaration for. A range check would let a transitive bump through
unnoticed.

`mobile-native/app.json` is checked too — `NSMicrophoneUsageDescription` must be a
non-empty string and `UIBackgroundModes` must contain `"audio"`. Without the first,
iOS denies the microphone outright; without the second, a backgrounded call goes
silent. Neither failure is visible in a simulator run or a unit test.

## 9. CODEOWNERS

**PASS**, with one deliberate incompleteness.

`.github/CODEOWNERS` assigns `@hmcroody-alt` — **evidenced** from `git remote -v`
(`git@github.com:hmcroody-alt/CoinPilotX.git`), not invented. Blocks cover the lock's
own files, the coordinator, ownership policy, publisher, telemetry, the call adapter,
livestream audio, the token APIs, the tests, the dependency and native build surface,
and the baseline and policy documents.

The team handle `@hmcroody-alt/realtime-audio-owners` is **intentionally withheld**
behind a `TODO(repository administration)`. If a CODEOWNERS pattern names a user or
team that does not exist, GitHub does not error — it silently assigns no owner and the
rule fails open. Adding a handle before the team exists would create the appearance of
ownership with none of the substance.

The file header also states the load-bearing caveat: CODEOWNERS blocks nothing unless
branch protection has "Require review from Code Owners" enabled. See item 10.

## 10. Branch protection

**NOT CONFIGURED — specified only.**

`docs/realtime_audio_branch_protection.md` opens by saying so: *"This document
specifies settings. It does not claim they are active."* GitHub is unreachable from
this build environment (SSH returns `Forbidden`, HTTPS returns `403 from proxy`), so
the settings could not be applied and, more importantly, could not be **verified** —
and the mission forbids claiming otherwise.

The document contains the full settings table (require a pull request, one approval,
dismiss stale approvals, **require review from Code Owners**, require status checks,
require branches up to date, require conversation resolution, **do not allow bypassing
— including administrators**, no force pushes, no deletions), a required-status-checks
table naming the exact job display names from the workflow, the `audio-critical-change`
label spec, and a five-step verification procedure.

Step 2 is the decisive one and is written to be uncomfortable: *"If the merge button
offers a bypass, 'Do not allow bypassing the above settings' is off and the lock is
advisory."* Until an administrator runs that procedure, every other layer in this lock
is a strong recommendation rather than a barrier.

**This single item is why the overall verdict is PARTIAL.**

## 11. Policy forbidding unrelated missions from editing protected paths

**PASS.** `docs/realtime_audio_change_policy.md` states the rule and its five
conditions, enumerates the unrelated missions by name (Marketplace, Advertising,
Premium, Crypto, Profile, Feed, Settings, Search, general UI), and explains the
content-based `bot.py` rationale so the exception does not read as an inconsistency.

It closes with a section on what automation can and cannot prove — the honest boundary
of the whole exercise. Every rule in the policy has a corresponding automated check, so
the document describes enforcement rather than substituting for it.

## 12. Runtime invariants in production builds

**PASS**, and not `__DEV__`-gated.

`mobile-native/src/core/realtimeAudioInvariants.ts` runs unconditionally in production.
Eight invariant ids: `multiple_microphone_owners`, `duplicate_microphone_tracks`,
`duplicate_publication`, `conflicting_route_state`, `stale_cleanup_of_newer_session`,
`viewer_publication_attempt`, `session_active_without_owner`,
`terminal_room_reconnect_attempt`. A test asserts the covered set is exactly 8, so an
invariant cannot be dropped silently.

Three design decisions, each deliberate:

**It reports and never repairs.** If this module also took corrective action it would
become a second decision-maker for audio state, which is precisely the failure mode
the entire boundary exists to prevent.

**It is wired only into branches that already reject.** The stale-lease return in the
engine, the viewer-forbidden return in the publisher, and the post-hoc duplicate
reconciliation. Runtime behavior is byte-identical; the only addition is a counted
event. This is how section 12 was satisfied without violating the constraint *"do not
change runtime behavior."*

**It cannot leak.** `safeDetail()` replaces any value not matching `/^[a-z0-9_]{1,32}$/`
with `"unspecified"`, so a token, URL, or user id cannot reach telemetry. A test feeds
it `wss://…?token=eyJhbGciOi` and asserts the replacement. History is bounded at 32
entries; a test fires 50 reports and asserts counts reach 50 while `recent.length <= 8`.

Escalation to `RealtimeAudioInvariantError` is opt-in via
`setRealtimeAudioInvariantPolicy`, off by default, and limited to
`session_active_without_owner`. Crashing a live call to report a state problem would
cause the outage it was built to detect.

18 tests, all passing, including sink-failure resilience and non-`__DEV__` operation.

## 13. Feature flags and kill switch with an exclusivity test

**PASS.** `LIVESTREAM_AUDIO_V2_ENABLED` is server-driven → client `audioV2Enabled`.
`normalizeLiveAudioV2Flag` requires strict `raw === true`, so `"true"`, `1`, and
`"yes"` all normalize to off — a misconfigured string cannot silently enable a new
audio path. `resolveLiveAudioPath()` returns `"v2_isolated" | "v1_legacy"`, default
`v1_legacy`, with **no local override**: the kill switch cannot be defeated from the
device.

Exclusivity is tested over a ten-input matrix: exactly one path is always selected,
never both, never neither. A further test asserts
`Object.keys(require("../../live/liveAudioFlags"))` does **not** contain
`setLiveAudioV2Enabled` — the absence of a local setter is itself enforced, not just
currently true.

## 14. Telemetry baseline and rollback thresholds

**PASS.** `realtimeAudioTelemetry.ts` gains one event-name union member,
`invariant_violation`, carrying the invariant id in `failureCategory` and the owning
module's action in `outcome`. No emitter, sink, sampling, or redaction behavior
changed.

Rollback thresholds are written in `docs/realtime_audio_release_checklist.md`: a
5-percentage-point regression in audio session activation success against the recorded
baseline, or activation failures exceeding 1% of sessions, triggers rollback. Numeric
thresholds rather than judgement calls, because "audio seems worse" does not survive
contact with a release decision at 2 a.m.

**Gap:** the *numeric* production baseline for those percentages is NOT RECORDED — no
production telemetry was available here. The thresholds are relative to a baseline the
first instrumented release must capture.

## 15. Release checklist

**PASS.** `docs/realtime_audio_release_checklist.md` is gate-driven: if the change gate
exits 0, the checklist is not required, so it does not become paperwork attached to
unrelated releases.

It contains the automated validation table, a build identity block instructing *"If any
of these is unknown, write NOT RECORDED. Do not write a plausible value"*, the flag
state, an eight-row **physical audible validation** table, the telemetry watch with the
thresholds above, a three-tier rollback ordering (flag → build → tag), and sign-off.

Its governing sentence: *"The only acceptable positive result is that a person heard
the audio."* A green test run is evidence that the invariants are stated, not that
sound came out of a speaker.

## 16. `audio-critical-change` PR label

**PARTIAL — specified and warned on, not enforced.**

The label is named in the manifest's `declaration` block, required by the change
policy, and checked by the workflow's `label` job. That job emits `::warning::` and
does **not** fail the build, by choice: failing a build over a missing label teaches
people to add labels rather than to validate audio. The hard gate is the `declaration`
job, which exits 1.

**The label does not exist in the repository yet and the automation has not been
verified running**, because GitHub is unreachable from here. This is stated rather than
claimed, per the mission's explicit instruction.

## 17. Snapshot tag

**PASS locally, NOT PUSHED.**

`realtime-audio-stable-v1` was created as an annotated tag at
`ce03e160eaf4649a8e02bc3b609a3182ca9d3859`, verified with
`git rev-list -n1 realtime-audio-stable-v1`. It did not previously exist; it has not
been moved or overwritten, and the tag message states that a later verified state gets
`v2` so the meaning of `v1` cannot change retroactively.

The tag deliberately points at the state **before** this hard-lock, so that rolling
back to it returns the audio foundation exactly as it was heard, with no
protection-layer code in the path. The hard-lock is expected to be behavior-identical,
but a rollback target should not depend on that expectation being true.

The tag message records the attested results, the byte-identity evidence linking
`ce03e160` to `b252a255`, the rollback command, and how to confirm the rollback worked
— by ear.

**Requires:** `git push origin realtime-audio-stable-v1` from a machine with GitHub
access.

## 18. Documented safe extension points and forbidden patterns

**PASS.** `docs/realtime_audio_safe_extension_points.md` gives five allowed extensions,
each with a "where / how / not" structure, and six forbidden patterns, each with a
"why it is fatal" explanation rather than a bare prohibition. A rule whose reason is
unstated gets deleted by the next person who finds it inconvenient.

It explains why the `expo-av` allowlist is frozen rather than fixed, and ends with the
pre-PR gate command so a developer can check themselves before CI does.

## 19. Required validation

**PASS.** Every step executed and measured.

| # | Step | Result |
| --- | --- | --- |
| 1 | Critical audio suite | PASS — 10 suites / 149 tests / 4.6 s |
| 2 | Full audio suite | PASS — 17 suites / 227 tests / 5.5 s |
| 3 | Architecture tests (native) | PASS — 21 tests |
| 4 | Architecture tests (backend) | PASS — 13 tests, `OK` |
| 5 | Contract suite | PASS — 42 tests |
| 6 | Backend token grants + webhook route owner | PASS — 4 tests, `OK` |
| 7 | TypeScript compilation | PASS — no errors |
| 8 | Inject a forbidden API, prove CI fails | **PASS** — `setAppleAudioConfiguration` added to `SettingsScreen.tsx`; Jest `EXIT=1`, 2 failed / 19 passed, naming the file and marker; Python `EXIT=1`, `FAILED (failures=1)`, naming the file plus the rule's reason |
| 9 | Revert the injection | **PASS** — restored, `git diff --name-only` returned 0 files, Python re-ran 13 tests `OK`, and a repository-wide grep confirms the marker now appears only in the allowlisted engine |
| 10 | Change a protected path without a declaration, prove the gate fails | **PASS** — `EXIT=1` with both failures reported; performed via `--changed-files-from` so no repository mutation occurred and no revert was needed |
| 11 | Confirm a clean tree | **PASS** — no `.orig`, `.bak`, `.tmp`, injected, or log artifacts remain anywhere in the working tree |

No intentional failure files or test mutations remain in the repository.

**Not run:** native build verification (`npx expo prebuild --platform ios` / EAS). No
macOS or Xcode toolchain exists in this environment. The workflow's `native-build` job
runs it in CI, and the release checklist blocks on it.

## 20. Do not over-freeze

**PASS.** Governance and automation only. Nothing was made read-only, no directory was
locked, and no developer is prevented from working.

The specific decisions that honor this:

The architecture scan runs on **every** pull request, but the expensive suites are
gated on `detect` — a forbidden call added to an unprotected screen is exactly the case
a fully gated scan would miss, while running the full audio suite on every CSS change
would teach people to ignore CI.

The declaration is required only for files inside the manifest. Indirect
audio-affecting paths (navigation, app lifecycle, auth/token services, backend room
authorization, dependency files) trigger the *test suites* through a separate
`indirect` output but do **not** demand a declaration, because they are not part of the
audio implementation.

`bot.py` is content-matched rather than path-protected, so ordinary backend work
proceeds untouched.

The label job warns rather than fails.

The six legacy `expo-av` files were frozen, not rewritten, because rewriting them would
have changed working runtime behavior — the one thing the mission forbids.

## 21. Runtime behavior change

**NONE.** Stated precisely, because this is the mission's hardest constraint.

No `AVAudioSession` category, mode, or activation call was added, removed, or
reordered. No microphone track is created, published, unpublished, muted, or unmuted at
a different time. No remote audio subscription changed. No route selection changed. No
cleanup ordering changed — the lease-generation check still returns `false` for a stale
lease at the same point with the same value. No ownership priority or outcome changed.
No dependency version changed. No flag default changed.

Three protected runtime files were touched, all additively:
`realtimeAudioEngine.ts` (+21), `realtimeMicrophonePublisher.ts` (+24),
`realtimeAudioTelemetry.ts` (+6). Every edit sits inside a branch that already returned
a rejection. The only observable difference is a counted, privacy-safe telemetry event
where there was previously silence.

The one deletion is compile-time only: the `export` keyword on
`PULSE_LIVE_PORTRAIT_VIDEO_RESOLUTION`, verified to have zero importers before removal.
`npm run typecheck` passes.

## 22. What remains, and who must do it

Four items require a person with GitHub administrative access and a physical device.
None can be completed from this environment, and none is silently assumed.

**On GitHub (administrator):**

1. Push the branch and the tag: `git push origin codex/store-dashboard-live` then
   `git push origin realtime-audio-stable-v1`.
2. Apply branch protection per `docs/realtime_audio_branch_protection.md`, then run its
   five-step verification. Until step 2 of that procedure shows no bypass on the merge
   button, this lock is advisory.
3. Create the `audio-critical-change` label, and create the
   `@hmcroody-alt/realtime-audio-owners` team **before** uncommenting the team line in
   `.github/CODEOWNERS` — a nonexistent team makes the rule fail open silently.

**On a device (release owner):**

4. Complete the seven-row physical audible validation in
   `reports/realtime_audio_change_declaration.md` and record the results, plus the
   device model, iOS version, and build number that are currently NOT RECORDED in
   `reports/realtime_audio_verified_baseline.md`.

---

## Summary table

| # | Section | Verdict |
| --- | --- | --- |
| 1 | Verified baseline document | PASS |
| 2 | Machine-readable protected-path manifest | PASS |
| 3 | Architecture tests blocking bypasses | PASS |
| 4 | Narrow public API | PASS |
| 5 | Contract tests for verified invariants | PASS |
| 6 | Golden-flow critical suite | PASS |
| 7 | Change-detection gate + declaration | PASS |
| 8 | Dependency lock | PASS |
| 9 | CODEOWNERS | PASS |
| 10 | Branch protection | **NOT CONFIGURED** |
| 11 | Unrelated-mission policy | PASS |
| 12 | Runtime invariants in production | PASS |
| 13 | Feature flags and kill switch | PASS |
| 14 | Telemetry baseline and rollback thresholds | PASS |
| 15 | Release checklist | PASS |
| 16 | `audio-critical-change` label automation | **PARTIAL** |
| 17 | Snapshot tag `realtime-audio-stable-v1` | PASS (local; **not pushed**) |
| 18 | Safe extension points and forbidden patterns | PASS |
| 19 | Required 11-step validation | PASS |
| 20 | Do not over-freeze | PASS |
| 21 | Runtime behavior change | NONE |
| 22 | Remaining human actions | 4 items, all documented |

**Overall: PARTIAL** — complete and verified in code, CI configuration, testing,
ownership, runtime enforcement, and documentation; blocked on GitHub server-side
settings and physical device validation that this environment cannot perform, and that
the mission explicitly forbids claiming without verification.

---

## Files delivered

**New**

```
config/realtime-audio-protected-paths.json
scripts/realtime_audio_change_gate.py
.github/workflows/realtime-audio.yml
.github/CODEOWNERS
mobile-native/src/core/realtimeAudioInvariants.ts
mobile-native/src/core/__tests__/realtimeAudioArchitecture.test.ts
mobile-native/src/core/__tests__/realtimeAudioContracts.test.ts
mobile-native/src/core/__tests__/realtimeAudioInvariants.test.ts
reports/realtime_audio_verified_baseline.md
reports/realtime_audio_change_declaration.md
reports/realtime_audio_hard_lock_report.md
docs/realtime_audio_branch_protection.md
docs/realtime_audio_change_policy.md
docs/realtime_audio_release_checklist.md
docs/realtime_audio_safe_extension_points.md
```

**Modified**

```
mobile-native/src/core/realtimeAudioEngine.ts          (+21, telemetry only)
mobile-native/src/core/realtimeMicrophonePublisher.ts  (+24, telemetry only)
mobile-native/src/core/realtimeAudioTelemetry.ts       (+6,  one union member)
mobile-native/package.json                             (+3,  critical suite membership)
tests/protection/test_realtime_audio_architecture.py   (+246/-54, manifest-driven)
```

**Tag**

```
realtime-audio-stable-v1 -> ce03e160eaf4649a8e02bc3b609a3182ca9d3859  (local, unpushed)
```
