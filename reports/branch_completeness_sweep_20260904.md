# Branch completeness sweep — 2026-09-04

Head of record: `integration/all-work-20260904` @ `e61c7cd5`.

The question this sweep answers is not "which branches were merged" but "is
there any work on any ref that is not present in HEAD". Merge status is a poor
proxy: most of this repo's branches were absorbed by re-implementation rather
than by merge commit, so `git branch --merged` under-reports badly in one
direction and `git log HEAD..branch` over-reports badly in the other.

## Accounting

Every ref is classified. There are no UNKNOWNs.

| | Refs |
|---|---|
| Total refs (local + `origin` + `ro`, excluding the remote heads themselves) | 131 |
| Ancestor of HEAD — every commit already in history | 87 |
| Patch-equivalent — `git cherry` finds no unapplied patch | 18 |
| Required content inspection | 26 |
| **Unclassified** | **0** |

The 26 are the local/`origin`/`ro` copies of 9 distinct branches. Each is
resolved below.

## Method

`git cherry` alone is not sufficient: patch-ids drift the moment context
changes, so a branch whose work was re-implemented reads as unapplied. For each
of the 9, the change set was taken from its own merge-base and then compared
against HEAD three ways, escalating only as far as needed:

1. blob identity per file (`git rev-parse HEAD:<f>` vs `git rev-parse <b>:<f>`);
2. symbol containment — every `def`/`class`/`export` on the branch must exist in
   HEAD's copy of the same file;
3. behavioural markers — for `bot.py` and the i18n catalogs, where file-level
   comparison is meaningless, the specific strings and keys the branch
   introduced.

A methodological note worth recording, because it produced a false result
first: under zsh an unquoted `$files` holding newlines expands as **one word**,
not one per line, so `git diff ... -- $files` silently matched nothing and
reported every branch as fully contained. All figures below are from the
per-file loop that replaced it.

## Verdicts

### INTEGRATED — content demonstrably present in HEAD (6)

| Branch | Evidence |
|---|---|
| `claude/happy-lalande-31ed6b` | Semantic merge, commit `31de436e`. `mobile-native/src/launch/sectionCapabilities.ts` and both suites present in HEAD. |
| `fix/status-rail-placeholder-translation` | `services/db.py` is a byte-identical blob; `bot.py` carries all three markers (`STATUS_LIST_FETCH_FAILED` ×3, `error_type=%s` ×5, the psycopg2 note ×1); the test file differs by one trailing newline. |
| `work/localization-i18n-wave` | Key-containment check across 11 locales × 2 catalogs: **0 branch keys missing from HEAD**. HEAD's catalogs are roughly 2× the branch's. All 27 non-catalog files present. |
| `origin/codex/continue-development-on-coinpilotxai` | A May-2026 legacy-PWA commit (7 lines of `pulse_page_html`). All three behaviours present in HEAD, rebranded Pulse → PulseSoc: the post-published fallback card, `PulseSoc intelligence temporarily unavailable`, `PulseSoc is warming up`. |
| `preserve/private-office-wip-20260902` | HEAD is a strict superset in all 16 differing files (`private_office_routes.py`: 1010 lines vs 120). Symbol containment found exactly one gap — `test_pack_is_get_only` — which HEAD replaced with `test_no_route_here_can_grant_a_tier`, documenting the rename in its own docstring: the pack is no longer GET-only because fact creation is a write, and the claim that survived is stated directly instead of being approximated by the method set. Strengthened, not lost. |
| `release/messenger-idempotency-p0` | Patch-equivalent; absorbed by `5bddee2a`. |

### SUPERSEDED — real work, obsoleted by the Agora migration (3)

RTC is Agora-only. These three branches are the LiveKit-era audio lineage. Their
code is written against a `Room` object with `localParticipant`,
`audioTrackPublications` and subscription state — a model the Agora SDK does not
have — so they cannot be ported, only re-derived, and HEAD has re-derived them.

| Branch | Not in HEAD | Why superseded |
|---|---|---|
| `codex/video-call-audio-controls-20260726` | `callMediaState.ts` + suite, `test_pulsesoc_call_livekit_grants.py`, one report + screenshot | Summarises LiveKit track publications. The user-visible functions — mute, video toggle, camera flip — are owned by `callSessionStore.ts` on Agora (`muteLocalAudioStream`, `muteLocalVideoStream`, `switchCamera`). |
| `codex/live-shared-audio-engine` | one report, `pulsesoc_live_audio_guest_join_repair_audit.py`, `pulsesoc_native_calls_audit.py` | Shares the LiveKit call engine with `useLiveBroadcastRoom`; both are Class-D orphans in the audio relevance audit. The audit script carries 5 LiveKit assertions. |
| `codex/governed-realtime-audio` | `realtimeAudioMediaPath.ts`, `realtimeRemoteAudioController.ts` + suites, `liveAudioConfiguration.test.ts`, 2 audit scripts, 2 LiveKit token-grant suites | Its host/viewer audio contract (host `playAndRecord`, viewer `playback`) is now owned by `liveAudioMatrix.ts` and covered by protected suites. Its LiveKit token-grant tests are replaced by `tests/protection/test_agora_token_generation.py` and `test_agora_rtc_provider_contract.py`. |

**One item on this list was not superseded and has been recovered.**
`codex/governed-realtime-audio` also carried `d72d8a48`, *"fix(native): keep
simulator session bootstrap recoverable"* — not audio, not LiveKit. The fix
reached HEAD; its test did not, and the gate it defended has since been
rewritten from `Device.isDevice` to `isLocalQaSession()`, leaving the invariant
unpinned. Recovered as
`mobile-native/src/session/__tests__/sessionStoreQaFallback.test.ts` in
`e61c7cd5`, rewritten against the shipping gate and verified by mutation
(widening the gate fails 6 of 13). This is the only work the sweep found at
risk.

### REJECTED WITH RECORD (1)

`claude/trusting-neumann-381eb0` — Item 4. Its telemetry reports the return code
of `audioDeviceModuleInitPlayout`, a method it adds by patching
`@livekit/react-native-webrtc`. There is no such package on HEAD. Rejection and
reasoning recorded in `d078df60`.

## Preservation

No branch was deleted, renamed or force-updated by this sweep. The three
SUPERSEDED branches remain the only copy of their reports, screenshots and audit
scripts, which is a sufficient reason to keep them: their code is obsolete, their
record of what was tried is not.

## Follow-ups

1. `useLiveBroadcastRoom.ts`, `realtimeAudioEngine.ts` and the rest of the
   superseded audio core are still in HEAD and still unreachable from the app
   entry point (265 tests, Class D of the audio relevance audit). Removal is
   tracked there, not here.
2. `isLocalQaSession()` permits the plaintext fallback on a *physical* device
   pointed at localhost, which `Device.isDevice` did not. That is a debug
   configuration and the new suite documents it rather than asserting it away;
   worth a decision if adhoc builds ever ship pointed at a LAN host.
