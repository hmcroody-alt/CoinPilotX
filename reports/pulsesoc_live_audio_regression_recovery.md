# PulseSoc Live Audio Regression Recovery

Date: 2026-08-02
Recovery branch: `codex/emergency-live-audio-recovery`
Production rollout: **NO-GO pending physical validation**

## Immutable references

- Last physically verified tag: `realtime-audio-stable-v1`
- Last known-good tag SHA: `fc25cd163b8802113df1b3b3d98cb7aab10891bb`
- Baseline report's physically verified commit: `ce03e160eaf4649a8e02bc3b609a3182ca9d3859`
- First bad commit: `8f99e54235ffd9954fc23af6b90bb4b5d5d82075`
- Regression-producing change: `feat(media-quality): add governed quality policy layer`
- Prior rollback commit: `c5e523d625166414573e618c1c043092794e7163`

`8f99e542` is the first post-tag commit that changes Live publisher room, capture, and publish configuration. The later `c5e523d6` commit restores Live publishers to the stable profile unless the new server-side `live_publisher_quality_enabled` flag is explicitly true.

## Root-cause status

Confirmed category after subsequent physical evidence: the first recovery gated media-quality V2, but the displayed exception is emitted only by the separate Live-audio V2 publisher engine guard. The existing general `audio_v2_enabled` token flag could therefore keep host/co-host sessions on the failing V2 startup path even when media quality was stable.

Exact component that deactivated the engine: **not yet proven**. The failure occurred after ownership/activation and the guard observed a stopped native engine, but the failed physical run did not include caller- and generation-complete tracing. It would be inaccurate to claim that the quality policy itself deactivated AVAudioSession: the policy modules are pure and contain no room or audio-session handle. Camera/RemoteIO interaction remains the leading hypothesis until a traced physical reproduction names the transition immediately preceding engine loss.

The fail-closed `REALTIME_AUDIO_ENGINE_INACTIVE` guard remains intact.

## Recovery changes

- Live publisher quality stays on the exact stable baseline by default and requires the additional server-authoritative `live_publisher_quality_enabled` opt-in.
- Live publisher audio V2 now independently requires `publisher_audio_v2_enabled`; absent or malformed values restore host/co-host to `v1_legacy` while leaving viewer rollout behavior unchanged.
- Media-quality flags and the resolved plan are snapshotted once per Live session.
- The ordered Live startup trace now records policy, owner, generation, AVAudioSession activation, room connection, microphone creation/publication, camera initialization, and post-camera active verification.
- Stop-capable lifecycle events record correlation ID, session, generation, current/requested owner, room, screen instance, profile, flags, caller, reason, and timestamp.
- The camera-then-engine-loss event order is reproduced by a fail-closed regression test.
- The post-camera lifecycle and trace suites now run inside `test:realtime-audio-critical`.
- The protected manifest and backend architecture test enforce the required lifecycle events/fields and critical-suite inclusion.

## Why the hard lock missed it

The protected manifest, forbidden API scan, workflow, CODEOWNERS, change declaration, stable tag, and release checklist existed before quality work. The quality-policy directory was added to the manifest by `8f99e542`, and no new direct AVAudioSession mutation was found outside the coordinator.

The gap was behavioral: the release-blocking critical command covered policy purity and baseline option equality but omitted `liveAudioConfiguration.test.ts`, the suite that owns post-camera engine stabilization. It therefore could not reproduce the physical camera/RemoteIO lifecycle. The guard now makes that suite release-blocking and enforces its inclusion from the manifest reader.

## Corrective commits

- `623afc8f` — `fix(live-audio): prevent quality policy from releasing active engine`
- `14ac7302` — `test(live-audio): lock engine activation through broadcast startup`
- `0f11cc0a` (pre-report-amend SHA) — `chore(audio-guard): extend protected paths to media quality lifecycle`

Local HEAD and remote SHA must be recorded after the final report commit/push. No push was performed during this recovery.

## Validation actually run

- Focused Jest: 4 suites, 112 tests passed.
- Release-blocking critical Jest: 15 suites, 295 tests passed.
- Python architecture enforcement: 15 tests passed.
- TypeScript `tsc --noEmit`: passed.
- `git diff --check`: passed before the focused commits.

An initial critical-suite invocation was run from the repository root and failed because the native `package.json` is under `mobile-native/`; it was immediately rerun from the correct directory and passed.

## Physical and rollout matrix

| Gate | Result |
|---|---|
| Corrected build installed | Not performed |
| Physical Live startup | Not observed |
| Host-to-viewer audible audio | Not observed |
| Five-minute Live | Not observed |
| Second Live without restart | Not observed |
| Audio-call regression | Not observed |
| Video-call regression | Not observed |
| Stable profile | Code/tests pass; physical result not observed |
| Elite profile | QA-only opt-in; physical result not observed |
| Feature flags | Stable by default; publisher quality requires explicit server opt-in |
| Rollback | Code path verified; remote kill-switch operation not exercised |

## Final judgment

**PARTIAL / production NO-GO.** The rollback, trace, regression test, and architecture enforcement are in place and local automated checks pass. PASS requires the complete two-device physical sequence in the emergency brief, including two consecutive Live sessions, five-minute audibility, audio call, video call, stable profile, and elite QA profile. Quality expansion remains stopped until that evidence exists.
