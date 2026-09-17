# Final branch and worktree ledger — 2026-09-17

Disposition of every branch, worktree and stash. 57 local branches, 93 remote
branches, 29 worktrees, 4 stashes.

## Release lineage

`origin/main` = `7dfeb7ac9af7ef55211e39718f00e7d2d5b2dd24`, reached by
fast-forward from `321f1c1c` with no force, pushed as a pinned SHA
(`git push origin <sha>:refs/heads/main`) rather than as a moving branch name.

The eleven commits:

| SHA | Subject |
|---|---|
| `4754979e` | notifications: every push must stamp the icon with the combined unread |
| `85d090be` | docs(translation): audit the existing translation stack before replacing it |
| `cd431e7f` | translation: expose Apple's on-device Translation framework to React Native |
| `db68e9b4` | translation: decide the provider in one place, and make the cloud earn its turn |
| `375ee888` | translation: one Apple session owner for the app, one identity per item |
| `1decc097` | i18n: a namespace the catalog actually resolves, in all eleven languages |
| `10f1c7fa` | translation: the screen stops deciding who pays |
| `20d16b57` | translation: put a seam where Apple's session cannot be constructed |
| `0e7769ca` | translation: run the module's tests, and fix the two that were never run |
| `6ba609b9` | translation: lock the pod, so the module ships instead of falling back |
| `7dfeb7ac` | docs(audio): discharge the translation declaration's owed battery |

The nine cherry-picked commits applied with zero conflicts, and `git range-diff`
reported all nine as `=` — byte-identical patches. The consolidation introduced
no drift of its own. `6ba609b9` and `7dfeb7ac` are new work created by this
mission.

## Archival refs pushed (19 on `archive/`)

Everything below is now on the remote and recoverable without this machine.

| ref | what it holds |
|---|---|
| `archive/consolidation-20260917/badge-writer-wip-live` | badge writer work in progress |
| `archive/consolidation-20260917/bold-hodgkin-media-work` | media work |
| `archive/consolidation-20260917/call-cas-race-and-docs` | call CAS race + docs |
| `archive/consolidation-20260917/cj-staging-reexport` | CJ staging re-export |
| `archive/consolidation-20260917/messenger-dashboard-e0cfdc2c` | messenger dashboard |
| `archive/consolidation-20260917/notif-reconciliation-selective` | notification reconciliation |
| `archive/consolidation-20260917/primary-checkout-dirty-state` | primary checkout, **tracked** modifications |
| `archive/consolidation-20260917/private-meetings-4a217dd5` | private meetings |
| `archive/consolidation-20260917/private-office-simplify` | private office simplification |
| `archive/consolidation-20260917/pushkit-voip-token-relaunch` | PushKit VoIP token relaunch |
| `archive/consolidation-20260917/session-lazy-auth-init` | lazy auth init |
| `archive/consolidation-20260917/stash-0` … `stash-3` | all four stashes |
| `archive/final-consolidation-20260917/cpx-bluegraphite-dirty` | bluegraphite, **tracked** modifications |
| `archive/final-consolidation-20260917/unruffled-hoover-qa-evidence` | QA evidence |
| `archive/final-consolidation-20260917/primary-checkout-untracked` | primary checkout, **untracked** files (11) |
| `archive/final-consolidation-20260917/cpx-bluegraphite-untracked` | bluegraphite, **untracked** files (4) |

The last two were added after discovering the first snapshots were index-derived
and therefore blind to files that had never been added. Both were built through a
temporary `GIT_INDEX_FILE` so no working tree was modified.

Also pushed: `feature/apple-on-device-translation` (`5cdec0c6`) and
`release/final-consolidation-20260917` (`7dfeb7ac`).

## Worktrees left deliberately untouched

| worktree | state | why |
|---|---|---|
| `/Users/hmcherie/Desktop/CoinPilotX` | 35 modified, 8 untracked | another session is actively working here; content archived twice, never written to |
| `cpx-bluegraphite` | 7 modified, 4 untracked | same; archived, not written to |
| `cpx-prefetch-iso` | 3 untracked (`dd/`, `dd-sim/`, `dd-sim-rel/`) | warm build rig; the untracked entries are DerivedData build products and must not be committed |

The primary checkout's tracked files were verified byte-identical to their
archive, which also proves no other session rewrote them during the mission.

## Worktrees already contained in `origin/main`

`ecstatic-northcutt-4b4e0f`, `thirsty-dijkstra-311568`, `unruffled-hoover-c25a30`,
`vigilant-edison-ccf61e`, `cjrepair-wt`, `cpx-bg-land`, `cpx-bluegraphite`,
`cpx-converge`, `cpx-final-consolidation`, `cpx-gate-8ad2b0da`,
`cpx-gate-e73ffc90`, `cpx-land-badge`, `cpx-land-notif`, `cpx-prefetch-iso`,
`cpx-profile-graphite`, `/tmp/callfix`, `/tmp/cpx-dropship-land`,
`/tmp/cpx-live`, `/tmp/pulse-music-merge-gate`, `/tmp/tfbuild`.

`cpx-bg-land` had shown a HEAD discrepancy earlier in the mission (`59a4e188`,
then `321f1c1c`). Re-checked at the end: it sits at `321f1c1c`, an ancestor of
`origin/main`. It was another session advancing a detached HEAD, not lost work.

## Nothing deleted

No branch was deleted, no worktree was removed, no stash was dropped, no
untracked file was discarded, and no `reset --hard` or broad `checkout --` was
run at any point. `nostalgic-neumann` remains in place per standing instruction.
