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

## Independent re-verification by a second method (added at `aba2adbb`)

The sweep above scoped each branch to its own change set from its merge-base.
That is the right question to ask, but it shares an assumption with itself: if
the merge-base were computed wrongly, the error would be invisible to a re-run
of the same method. So the accounting was re-checked with a deliberately
different and broader query, over **every** ref rather than the 26:

```
git diff --diff-filter=A --name-only HEAD <ref>     # files the ref has, HEAD lacks
```

This asks "what files exist on this ref that are not in HEAD at all", which
ignores merge-bases entirely. It over-reports by construction — it cannot tell a
file HEAD never received from one HEAD **deliberately deleted** — and that is
precisely why it is a useful cross-check: every hit must be explained, and an
unexplained hit would be a real gap.

**105 refs returned a non-empty set. All are explained; the count of
unexplained files is 0.**

The result that collapses it: the file sets are *shared*, not per-branch. Six
unrelated branches (`backup/translation-current`, `feat/business-profile-live`,
`release/undx-nexus-core-v4`, `integration/release-backend-deploy`,
`codex/undx-translation-release`, `backup/undx-fixes-3456cd73`) return **byte-identical
46-file sets**, and the larger sets are supersets of it. This is not 105 branches
holding unique work; it is one ancestral set that HEAD deleted, seen from 105
vantage points.

Each of the 46 was traced to its deletion commit on the HEAD lineage:

| Files | Deleted by | Nature |
|---|---|---|
| 38 | `f93e7ce3 chore(rtc): fire LiveKit` | The Agora migration. LiveKit audit scripts, `static/vendor/livekit-client.umd.js`, the live-studio JS, `test_livestream_contract.py`. |
| 4 | `f8fa3e6c feat(marketplace/ads): … PulseSoc iOS project rename` | `PulseSocNative/*` entitlements, UITests `Info.plist`, appicon. |
| 3 | `a770e01d feat(brand): replace PulseSoc logo across platform` | Renamed brand assets. |
| 1 | `943197f2 feat(premium): add Profile OS subscription entry and control center` | `PremiumScreen.tsx` — see below. |

Three branches absent from the verdict tables above were resolved individually:

- **`codex/undx-v3-preservation-20260719`** — its 36 files are a strict subset of
  the shared 46. Nothing of its own.
- **`codex/agora-rtc-migration.lock.moved.5`** — adds exactly one file, the
  Stripe patch, deliberately deleted by `f6b81781 chore(mobile): delete the
  orphaned Stripe patch`. (The same single file explains 17 of the 1-file refs.)
- **`codex/profile-posts-production`** — adds ten. Four are LiveKit by name (the
  `@livekit+react-native-webrtc` patch, which has no package to patch on HEAD,
  and three LiveKit token-grant protection tests replaced by
  `test_agora_token_generation.py` and `test_agora_rtc_provider_contract.py`).
  Five are the media-quality trio and its tests. One is `ExportOptions-AppStore.plist`.

### Two supersessions that had to be read, not counted

Both look identical to work loss until HEAD's replacement is opened.

**`PremiumScreen.tsx` → `PremiumCenterScreen.tsx`.** The delete and the add are
in the *same commit*, `943197f2`. HEAD's replacement states the relationship in
its own header — *"This screen replaces the old sales-only `PremiumScreen`"* —
and `AppNavigator.tsx:581` still routes the screen named `"Premium"` to it. 380
lines became 1818.

**`mediaQualityPolicy.ts` / `mediaQualityFlags.ts` / `mediaQualityTelemetry.ts`
→ `mediaAdaptationController.ts`.** This one is *not* a superset — 1059 lines
became 505 — so size alone would have read as loss. It is not. The branch
modules are LiveKit-shaped: `buildRoomQualityOptions()` returns options for
`new Room({...})`, and their only consumers are `useLiveBroadcastRoom.ts` and
`useNativeCallRoom.ts`, the Class-D orphans. `f93e7ce3` deleted all three. HEAD's
replacement is a pure reducer that by its own docstring *"does not touch a Room,
a track, the audio session, or the microphone"* — deliberately narrower, because
a second owner of media state is the failure mode the audio policy exists to
prevent. The shrink is the point.

One live-looking reference survives: `liveSession.ts:72` still names
`parseMediaQualityFlags`. It is prose inside a comment, not an import, and the
module it names is gone. Cosmetic, filed below.

### The one item this re-verification adds

`mobile-native/ios/ExportOptions-AppStore.plist` was removed by the iOS project
rename and **no equivalent was created under the new `PulseSoc/` name** — HEAD
has no `ExportOptions` file anywhere. This is already known: HEAD's own
`realtime_audio_change_declaration.md` records both casualties of that rename and
calls this one *"a release-packaging concern"*. But only the other half — the
orphaned XCUITest — had reached the follow-up register. The plist is now filed
there too. Its content is nine lines and recoverable from `f8fa3e6c^`; the cost
is that an App Store Connect upload has no checked-in export options.

**No verdict above changed.** The re-verification neither found work at risk nor
overturned a classification; it raised one packaging follow-up and confirmed the
accounting by a method that could have contradicted it.

---

# Worktree, stash and scratch sweep — same date

The branch sweep above covers refs. This covers everything work can hide in that
is *not* a ref: working trees, the stash reflog, and untracked files.

## Worktrees

19 registered; 13 have a directory, 4 are prunable (their directories are gone),
2 are session-mount paths that no longer resolve.

**Uncommitted source work found: none.** The only dirty worktree,
`CoinPilotX-founding-path`, has two untracked entries — `mobile-native/ios/Pods`
and `mobile-native/node_modules`, both empty build-artefact directories.

The 4 prunable registrations are safe to lose: the only one on a branch of
interest, `release/messenger-idempotency-p0`, is patch-equivalent to HEAD and was
absorbed by `5bddee2a`. No worktree was removed by this sweep.

## Stashes

Four, all checked by symbol containment against HEAD.

| Stash | Verdict |
|---|---|
| `{0}` *wip: premium command center awaiting 10-locale completion* | Fully in HEAD, **including the thing it was waiting for**: `CommandCenterSection`, `COMMAND_MODULES`, `COMMAND_SPACES` and the chip styles are all present, and `premium.commandCenter` resolves to 60 keys in every one of the 11 locales. |
| `{1}` *undx-v3-pre-integration-preservation-20260719* | Contained bar one file — see below. |
| `{2}`, `{3}` autostash | Subsets of `{1}`'s working state, same verdict. |

The symbol gaps reported against stashes 1–3 are noise or intent, not loss. They
fall into two groups: LiveKit endpoints (`api_pulse_live_livekit_token`,
`api_pulse_livekit_webhook`, `api_livekit_webhook`) deliberately removed by the
Agora migration, and local variables inside refactored function bodies (`url`,
`now`, `counts`, `me`, `glow`) that the containment regex cannot distinguish
from exports.

`mobile-native/ios/PulseSocNative.xcodeproj/project.pbxproj` reads as absent
because the iOS project was renamed `PulseSocNative` → `PulseSoc`. Same project.

### The one real find

`PulseSocNativeCameraStudioQATests.swift` — a 271-line XCUITest — existed
nowhere but in the stash reflog, byte-identical across all three stashes, at a
path the project rename orphaned. Rescued to `qa/ios-uitests/` in `3bbb6bfd`,
with a README stating what it would cost to run again. Not wired to a target,
not in CI.

**No stash was dropped.** They remain the record of what the working tree looked
like at those points.

## Scratch and static

| Check | Result |
|---|---|
| Tracked scratch (`.orig`, `.rej`, `.bak`, `~`, `.DS_Store`, `zz*`) | 0 |
| Untracked files in the integration worktree | 0 |
| Tracked files over 5 MB | 0 |
| Root `*.md` also duplicated under `reports/` | 0 |
| `.fuse_hidden*` tracked | 0 |

Two things were deliberately **not** cleaned:

1. **61 `.fuse_hidden*` files in the shared `~/Desktop/CoinPilotX` checkout.**
   They are matched by `.gitignore:28`, untracked, and invisible to every gate.
   They are also, by construction, files some process still holds open — and
   that checkout is shared with other sessions. Deleting them buys nothing the
   release cares about and can break a running session.
2. **~70 mission writeups at the repo root.** These are traceability artefacts,
   which this consolidation is supposed to be protecting, not deleting. Moving
   them under `docs/` is a 70-file rename that would land in the same push as
   the release and make its diff materially harder to read. Filed as a
   follow-up instead.
