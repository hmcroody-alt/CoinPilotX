# Final consolidation inventory — 2026-09-17

What existed when this mission began, and where each piece of it ended up.

## The starting picture

29 worktrees, 57 local branches, 4 stashes, and two dirty working trees that
belonged to sessions other than this one. `origin/main` was `321f1c1c` at the
moment of the freeze and moved once during the mission — it had been `a88f8442`
earlier in the day, which is the reason every uniqueness question below is asked
against a re-fetched `origin/main` rather than a remembered one.

## The one thing that was genuinely at risk

The Apple on-device translation feature was complete, tested, and **inert**.

`mobile-native/package.json` declared the module and
`modules/pulse-apple-translation/ios/PulseAppleTranslation.podspec` described how
to build it, but `mobile-native/ios/Podfile.lock` had never been regenerated.
Expo autolinking discovers modules at `pod install` time, so with the lock file
unchanged there was nothing to link. On a device `requireOptionalNativeModule`
returned nil and every call site degraded — correctly and silently — to
`native_bridge_unavailable` with `permitsCloudFallback: true`.

That is why it survived review: the app was not broken, it was quietly paying
the cloud for something it had been built to do on-device. Nothing in the test
suite could see it, because the fallback is the designed behaviour on a device
that lacks the module.

Closed by `6ba609b9`, a six-line `Podfile.lock` delta. The proof that it worked
is in `FINAL_DEVICE_INSTALL_REPORT.md`: both binaries now export 86
`PulseAppleTranslation` symbols. That marker *inverts* between lineages — it
cannot be present in a build made before the lock was regenerated — which is
what makes it evidence rather than decoration.

## Uniqueness: what was actually local-only

`git cherry origin/main <ref>` was run against the final `origin/main`
(`7dfeb7ac`) for every head not already contained in it:

| ref | SHA-local commits | patch-unique |
|---|---|---|
| `main` (local) | 10 | 0 |
| `claude/confident-fermat-ba5133` | 1 | 0 |
| `claude/laughing-hypatia-e9b44e` | 2 | 0 |
| `claude/objective-elgamal-8060a9` | 1 | 0 |
| `claude/stupefied-hawking-c037f5` | 2 | 0 |
| `claude/trusting-lamport-25f78b` | 1 | 0 |
| `feature/apple-on-device-translation` | 8 | 0 |
| `60254e66` (notif-converge) | 1 | 0 |
| `8f163d6a` (music-gate) | 1 | 0 |

Every one is patch-equivalent to something already upstream. "Ahead of main" was
never the same question as "carries unique work", and here it never once meant it.

Local `main` deserves its own note: `merge-base --is-ancestor main origin/main`
is false, so it looks like a divergent lineage worth merging. `git diff --stat
origin/main main` is 175 files, +827/−23956 — local `main` is the *older* side of
a parallel history. Merging it would have reverted work. It was correctly left
alone.

## The gap found at the end

The Stage 1 snapshots were built from the index, so they captured modifications
to tracked files and nothing that had never been added. Twelve untracked files
across two worktrees — another session's assistant-connection and Mux-backfill
work, and the blue-graphite theme module — existed nowhere but local disk.

Two further snapshots were built through a temporary `GIT_INDEX_FILE`, parented
on the original archives, and pushed. Neither working tree was touched; both
still report the same entry counts they did before (35 and 7).

This is the sort of thing that reads as complete right up until the disk dies.

## Money movement

`git diff --name-only 321f1c1c..7dfeb7ac` matches nothing against
`stripe|payout|payment|marketplace|settle|ledger|transfer|connect`. Production
Railway holds live keys only; none of `MARKETPLACE_PAYOUT_WORKER_ENABLED`,
`..._OWNER_AUTHORIZED`, or any transfer or seller-fee variable is set. The gates
were closed before this mission and are closed after it. No test credential
reached production.

## Protected audio

No protected audio source file changed. The three files that tripped the gate —
`Podfile.lock`, `package.json`, `package-lock.json` — are `dependency_watch`
entries, not audio code. `scripts/realtime_audio_change_gate.py` initially
rejected the range on *staleness* rather than naming: the declaration predated
the Podfile.lock commit, and a declaration that is an ancestor of the change it
describes cannot describe it. `7dfeb7ac` appends the addendum as a descendant,
which is what the gate is actually asking for.
