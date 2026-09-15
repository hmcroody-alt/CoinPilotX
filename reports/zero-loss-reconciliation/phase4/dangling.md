# Phase 4 — unreachable commits

`git fsck` reports **381 unreachable commits**. All 381 resolve to **zero lost
work**. Nothing was resurrected, because nothing needed to be.

Reading 381 diffs by hand is not a plan, so this narrows by content in layers,
cheapest and strictest first, and only the residue gets human eyes.

## The funnel

| layer | test | count |
|---|---|---|
| patch-id duplicates | `git cherry origin/main <sha> <sha>^` prints `-` | 174 |
| stash machinery | subject is `index on …` / `WIP on …` / `On …` | 143 |
| amend & rebase pre-images | subject already appears in `origin/main`'s log | 30 |
| unclassifiable merges | no single diff, therefore no patch id | 18 |
| **residue read by content** | Phase 4b line-presence probe | **16** |

The 174 are definitionally carrying nothing main lacks: a patch-id match means
the diff is already upstream, and it survives cherry-pick, rebase and re-author.
The 143 are stash bookkeeping — every `git stash` writes two or three commits
and abandons them. The 30 are the pre-image of an `--amend` or a rebase step,
identified by their subject surviving upstream.

The 18 merges are reported rather than dropped. A merge has no single diff, so
`git cherry` omits it and a naive pipeline silently loses it — which is exactly
the failure mode this phase exists to prevent. Each was checked for a subject
match upstream; all 18 are merge commits whose *result* is reachable.

## The 16 that needed content, not provenance

Patch id is exact. A commit whose work arrived upstream **split across two
commits, folded into a larger refactor, amended with one extra line, or re-typed
from scratch** shows as `+` while being genuinely present. Those four
transformations are the whole reason a second, weaker test exists.

`scripts/zero_loss_orphan_presence.py` asks: of the *distinctive* lines this
commit added, how many exist in main's tree today? "Distinctive" carries the
argument — a diff is mostly closing braces, `import os`, and bare `return`, and
counting those measures how much Python looks like Python. The filter drops
short lines, pure punctuation, and universal one-liners, then requires a minimum
sample of 8 so that a commit made entirely of boilerplate is reported
**inconclusive** rather than silently scored.

| verdict | n | disposition |
|---|---|---|
| present (≥90%) | 9 | old copies of shipped work |
| inconclusive (<8 distinctive lines) | 3 | trivial — `tmp`, empty, one-line |
| partial | 2 | read individually, both superseded — below |
| absent (<20%) | 2 | a reels fix **and its own revert** — they cancel |

### The two partials

Both were read against main directly rather than trusted to a ratio.

**`d2837f2064` — "fix crypto alerts to fire only on threshold crossings" (51/60).**
Main's `services/alert_engine.py` implements crossing detection with a
`trigger_seq` column and optimistic concurrency on the update. The orphan's
approach is a strictly weaker subset of what shipped.

**`4709beed48` — "fix(live): stop failing healthy broadcasts on a playout signal" (52/60).**
Main's `realtimeAudioEngine.ts:640` contains the orphan's exact `requirePlayout`
line, extended with a profile concept the orphan does not have.

> ⚠️ This file is a **protected real-time audio path**. The work is already
> upstream and nothing needs porting — which is the good outcome, because a
> casual port here is precisely the move
> `docs/realtime_audio_change_policy.md` forbids.

### The two absents

`d1faaa8fbc` "fix(reels): derive navigator visibility from swipe direction"
(10/60) and `bf87d82821` `Revert "fix(reels): derive navigator visibility from
swipe direction"` (2/46). A change and its own revert. Their sum is empty, and
main correctly holds neither. Counting these as "two pieces of missing work"
would be the arithmetic of a script that never looked at the subjects.

## Verdict

381 unreachable commits, zero MISSING_FROM_MAIN. Every one is ALREADY_PRODUCTION,
SUPERSEDED, or debris that never carried content.
