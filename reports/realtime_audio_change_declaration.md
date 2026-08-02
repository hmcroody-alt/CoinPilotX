# Real-Time Audio Change Declaration

**Change:** Mission D — PulseSoc real-time audio hard-lock
**Base commit:** `ce03e160eaf4649a8e02bc3b609a3182ca9d3859`
**Baseline of record:** `reports/realtime_audio_verified_baseline.md`
**Label required:** `audio-critical-change`
**Declared on:** 2026-08-02

> This is the first filled instance of this declaration. It covers the change that
> built the boundary itself. That is deliberate: a gate whose own introducing
> commit bypasses it is a gate nobody believes in afterwards.
>
> The template that used to live here is preserved verbatim at the bottom of this
> file, under "Template for the next change", so the next author has the blank
> form without needing to dig it out of git history.

## Why the change is required

The audio foundation was physically heard working on 2026-08-02 and nothing in the
repository prevented the next commit from silently taking it apart. A forbidden
`Audio.setAudioModeAsync` added to an unrelated media screen can steal the session
from a live call, and the symptom is silence on a real device in production — not a
failing build, not a type error, not a red diff in review.

Section 12 of the mission required runtime invariants that hold in **production**
builds, not only under `__DEV__`. Counting an impossible state at the moment it
occurs means reporting from the place that already detects it, and those places are
inside protected files:

- the stale-lease rejection lives in `realtimeAudioEngine.ts`
- the viewer publication refusal and the duplicate-track reconciliation live in
  `realtimeMicrophonePublisher.ts`
- the event vocabulary that carries the report lives in `realtimeAudioTelemetry.ts`

There is no way to observe those three rejections from outside the modules that
perform them. Instrumenting from a wrapper would mean re-deriving the state a second
time, which is exactly the second-decision-maker failure this boundary exists to
prevent. So these three protected files had to be touched, and every edit was
confined to a branch that already returned a rejection.

The remaining protected files changed are the boundary's own machinery: the manifest,
the two architecture-test readers, the gate script, CODEOWNERS, the workflow, and the
`package.json` script that names the critical suite. Those cannot be added from
outside themselves.

## Which feature required it

**Mission D — PulseSoc real-time audio hard-lock.** This is not an unrelated mission.
Its entire purpose is the protection of the audio foundation, so
`docs/realtime_audio_change_policy.md`'s unrelated-mission rule does not apply; the
policy's ordinary requirements (declaration, label, full validation, physical
re-validation before release) do.

No product feature was added. No new audio route was created. No audio behavior was
redesigned.

## Which protected files changed

Output of `python3 scripts/realtime_audio_change_gate.py` against this change set:

| File | Manifest category | What changed |
| --- | --- | --- |
| `mobile-native/src/core/realtimeAudioEngine.ts` | `shared_audio_session_coordinator` | +21 lines. Added the `realtimeAudioInvariants` import and one `reportRealtimeAudioInvariant({ id: "stale_cleanup_of_newer_session", action: "rejected" })` call inside the existing lease-generation mismatch branch, immediately before its unchanged `return false`. Also un-exported the dead constant `PULSE_LIVE_PORTRAIT_VIDEO_RESOLUTION`, which had no importers; it is still consumed internally by `PULSE_LIVE_VIDEO_CAPTURE_OPTIONS`. No control flow, no ordering, no session call changed. |
| `mobile-native/src/core/realtimeMicrophonePublisher.ts` | `microphone_track_and_publication_controller` | +24 lines. Added the same import and two reports: `duplicate_microphone_tracks` / `"reconciled"` after `reconcileDuplicates` has already unpublished extras (`removed > 0`), and `viewer_publication_attempt` / `"rejected"` inside the existing `canPublishMicrophone === false` branch, before its unchanged `forbidden` return. No publication path, no timeout, no reconciliation logic changed. |
| `mobile-native/src/core/realtimeAudioTelemetry.ts` | `audio_telemetry` | +6 lines. Added one member to the event-name union: `"invariant_violation"`. No emitter, sink, sampling, or redaction behavior changed. |
| `mobile-native/src/core/realtimeAudioInvariants.ts` | `runtime_invariant_monitor` | **New file.** The production runtime invariant monitor: eight invariant ids, six pure check functions, a bounded 32-entry history, `safeDetail()` vocabulary replacement so no free-form string (token, URL, user id) can reach telemetry, and a report sink that never repairs state. Escalation to `RealtimeAudioInvariantError` is opt-in via `setRealtimeAudioInvariantPolicy` and off by default. Runs unconditionally — it is not `__DEV__`-gated. |
| `mobile-native/src/core/__tests__/realtimeAudioInvariants.test.ts` | `critical_audio_tests` | **New file.** 18 tests: detection of all eight invariants (asserting the covered set size is exactly 8), privacy-safe emission, vocabulary replacement of a token-bearing detail, bounded history under 50 reports, no-throw for the seven already-handled ids, opt-in escalation, sink-failure resilience, non-`__DEV__` operation, and the four livestream-flag exclusivity tests over a ten-input matrix. |
| `mobile-native/src/core/__tests__/realtimeAudioArchitecture.test.ts` | `critical_audio_tests` | Now manifest-driven end to end, 21 tests. Added the `import_boundary` type and two tests: the audio core is importable only from the eight approved files, and every approved importer is a real file. |
| `mobile-native/src/core/__tests__/realtimeAudioContracts.test.ts` | `critical_audio_tests` | The 42 contract tests for every verified invariant, including all eight mixed-session transitions. |
| `tests/protection/test_realtime_audio_architecture.py` | `critical_audio_tests` | +246 / -54. Replaced a hard-coded two-file allowlist (which was narrower than reality — it did not know about `Audio.setAudioModeAsync`) with a manifest-derived reader: 13 tests across manifest integrity, forbidden APIs, import boundary, lease discipline, and dependency lock. |
| `config/realtime-audio-protected-paths.json` | `audio_governance` | **New file.** The single machine-readable source of truth, `manifest_version: 1`. Read by the Jest architecture test, the Python protection test, and the change gate, so the rules cannot drift from what CI enforces. |
| `scripts/realtime_audio_change_gate.py` | `audio_governance` | **New file.** The change-detection gate. Maps changed paths to manifest categories, applies content-based matching to `bot.py` via `backend_diff_patterns`, and rejects an unfilled or incomplete declaration. |
| `.github/workflows/realtime-audio.yml` | `audio_governance` | **New file.** Seven jobs: `detect`, `architecture`, `critical`, `backend`, `native-build`, `declaration`, `label`. `architecture` runs on every pull request regardless of `detect`, because a forbidden call added to an unprotected screen is precisely the case a gated scan would miss. |
| `.github/CODEOWNERS` | `audio_governance` | **New file.** Owner `@hmcroody-alt`, taken from `git remote -v`, not invented. The team handle is deliberately withheld behind a `TODO(repository administration)` because GitHub silently assigns no owner for a nonexistent team — the rule would fail open. |
| `mobile-native/package.json` | `dependency_watch` | +3 lines. `test:realtime-audio-critical` now includes `realtimeAudioInvariants.test.ts`. **No dependency version changed**; the pinned media stack is byte-identical to the baseline and the dependency-lock test asserts equality against it. |

Unprotected files also changed in this commit (no declaration consequence, listed for
completeness): `reports/realtime_audio_verified_baseline.md`, this file,
`docs/realtime_audio_branch_protection.md`, `docs/realtime_audio_change_policy.md`,
`docs/realtime_audio_release_checklist.md`,
`docs/realtime_audio_safe_extension_points.md`.

## Expected behavior change

**None.** Nothing a person holding a phone can hear, see, or measure should differ.

Stated precisely, because "none expected" is the claim the physical validation is
testing:

- No `AVAudioSession` category, mode, or activation call was added, removed, or
  reordered.
- No microphone track is created, published, unpublished, muted, or unmuted at a
  different time than before.
- No remote audio subscription changed.
- No route selection (speaker / receiver / Bluetooth) changed.
- No cleanup ordering changed. The lease-generation check still returns `false` for a
  stale lease at the same point, with the same value.
- No ownership arbitration priority or outcome changed.
- No dependency version changed.
- No feature flag default changed. `LIVESTREAM_AUDIO_V2_ENABLED` remains off, and
  `resolveLiveAudioPath()` still returns `v1_legacy` absent a strict server-sent
  `true`.

The single observable difference is additive telemetry: three previously-silent
rejection branches now emit an `invariant_violation` event carrying an enum id and an
enum-constrained detail. Each of those branches already returned the same rejection
before this change.

The one deletion — the `export` keyword on `PULSE_LIVE_PORTRAIT_VIDEO_RESOLUTION` — is
compile-time only and was verified to have zero importers before removal;
`npm run typecheck` passes.

## Regression risk

| Verified surface | Can this change affect it? | Why |
| --- | --- | --- |
| Audio call | No | Engine edit is confined to the stale-lease branch, which already returned `false`. A normal call never enters that branch; when it does, the outcome is unchanged. |
| Video-call audio | No | Same code path as audio call. Video capture options were not modified — only the visibility of a constant they consume internally. |
| Livestream host audio | Low | The publisher edits sit in `reconcileDuplicates` (post-hoc, after the unpublish has already happened) and in the `canPublishMicrophone === false` refusal. A host is permitted to publish, so it never reaches the refusal; the reconciliation report is emitted after the reconciliation completes. |
| Livestream guest audio | Low | Same publisher path as host, via the approved-guest publish gate. The gate logic itself is untouched. |
| Livestream viewer playback | No | Viewers do not publish. The new `viewer_publication_attempt` report fires only on the already-existing refusal, which viewers do not trigger in normal operation. |
| Speaker / receiver / Bluetooth routing | No | No routing call was added or moved. `checkRouteState` is a pure function that is not wired into any runtime path in this change. |
| Interruption recovery | No | No interruption handler was modified. |
| Cleanup | No | The lease-generation rejection is byte-identical in effect. This is the branch most likely to fire in the field, which is why it is now counted. |
| Mixed-session transitions | No | Arbitration priorities, outcomes, and the eight transitions are unchanged; the 42 contract tests covering them pass unmodified in substance. |

The residual risk that automation cannot retire: the monitor allocates a small object
per report and appends to a 32-entry ring. If an invariant fired in a tight loop this
would add allocation pressure on a hot path. Mitigation is structural — the history is
bounded, the detail is enum-constrained rather than string-formatted, and all three
call sites are on rejection paths that should be rare. Physical validation below is
what actually confirms it.

## Tests run

Measured 2026-08-02 on this change set, not claimed.

| Check | Command | Result |
| --- | --- | --- |
| Critical audio suite | `npm run test:realtime-audio-critical` | **PASS** — 10 suites, 147 tests, 4.5s test time / 15s wall |
| Full audio suite | `npm run test:realtime-audio` | **PASS** — 17 suites, 227 tests, 5.5s. Confirms the critical suite is the faster of the two, as section 6 requires. |
| Architecture (native) | `npm run test:realtime-audio-architecture` | **PASS** — 21 tests |
| Architecture (backend) | `python3 -m unittest tests.protection.test_realtime_audio_architecture` | **PASS** — 13 tests, `OK` |
| Contract suite | `jest src/core/__tests__/realtimeAudioContracts.test.ts` | **PASS** — 42 tests |
| Backend token grants | `python3 -m unittest tests.protection.test_call_livekit_token_grants tests.protection.test_livestream_audio_token_grants tests.protection.test_livekit_webhook_route_owner` | **PASS** — 4 tests, `OK` |
| TypeScript | `npm run typecheck` | **PASS** — no errors |
| Native build | `npx expo prebuild --platform ios --no-install` or an EAS build | **NOT RUN** — no macOS/Xcode toolchain in this environment. Required before release; the workflow's `native-build` job runs it in CI. |

Enforcement proofs performed and reverted, per section 19:

| Proof | Injection | Native result | Backend result | Reverted |
| --- | --- | --- | --- | --- |
| Forbidden API reaches an unprotected file | `setAppleAudioConfiguration` appended to `mobile-native/src/screens/SettingsScreen.tsx` | `EXIT=1`, 2 failed / 19 passed, named the file and the marker | `EXIT=1`, `FAILED (failures=1)`, named the file plus the rule's reason | Yes — restored from backup, `git diff --name-only` returned 0 files, backend re-ran 13 tests `OK` |
| Protected path changed without a declaration | `realtimeAudioEngine.ts` passed via `--changed-files-from` (no repository mutation) | — | `EXIT=1`: "is still the unfilled template" **and** "does not name these changed protected files" | N/A — no file was modified |
| Control: unprotected paths only | `MarketplaceScreen.tsx` + `bot.py` (non-matching diff) | — | `EXIT=0`, "No protected real-time audio path changed (2 file(s) inspected)" | N/A |

No intentional failure files or test mutations remain in the repository.

## Physical validation required

Automated tests show the invariants still hold. They cannot show that a human still
hears sound, and this change touches the coordinator and the publisher. Every surface
below is therefore **required** before this change reaches production, on a physical
device — not a simulator, which does not exercise `AVAudioSession` arbitration.

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

**NOT PERFORMED** is recorded honestly rather than left blank. This environment has no
physical device and no macOS build toolchain. The release checklist
(`docs/realtime_audio_release_checklist.md`) blocks on these rows, and its rule stands:
the only acceptable positive result is that a person heard the audio.

## Rollback procedure

- **Immediate mitigation (no app release):** none of these edits is flag-gated,
  because none of them changes behavior. The nearest server-side lever is
  `LIVESTREAM_AUDIO_V2_ENABLED=0`, which forces `resolveLiveAudioPath()` to
  `v1_legacy` — but it is already the default and disables the isolated livestream
  path, not this change. If telemetry volume from `invariant_violation` becomes a
  problem, disable that event at the telemetry sink; the monitor tolerates a throwing
  or absent sink by design (covered by the sink-failure-resilience test).
- **Code rollback:** `git revert <sha>` of this commit. It is safe to revert whole:
  the three runtime edits are additive telemetry, and reverting the manifest, tests,
  gate, workflow, and CODEOWNERS together removes the enforcement without leaving a
  half-configured boundary. Alternatively `git checkout realtime-audio-stable-v1`,
  the immutable snapshot of the physically-validated audio foundation.
- **Backend rollback:** none required. No backend file changed. `bot.py`,
  `requirements.txt`, and the LiveKit token grants are untouched, and the backend
  token tests pass unmodified.
- **Who to notify:** the real-time audio owner in `.github/CODEOWNERS`
  (`@hmcroody-alt`), plus whoever is on release duty for the affected build.
- **How to confirm the rollback worked:** a person places an audio call and hears
  both directions; a person joins a livestream and hears the host. Nothing less
  counts. A green test run after a rollback confirms only that the code compiles and
  the invariants are stated — it is not evidence of sound.

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
