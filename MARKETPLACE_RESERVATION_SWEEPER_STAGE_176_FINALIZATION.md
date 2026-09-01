# Stage 176 Finalization — Commit, Merge, Dry-Run Deploy

**Mission:** wire the durable marketplace reservation sweeper into production safely.
**Priority:** CRITICAL PAYMENT SAFETY.
**Date:** 2026-08-31 / 2026-09-01 UTC.

---

## FINAL VERDICT: **BLOCKED**

Everything that could be done inside this session was done and verified. The code
is committed, gated, and proven to merge cleanly onto `main`. It cannot reach
production because this environment has no push credentials for
`github.com:hmcroody-alt/CoinPilotX`. Railway builds `main` from GitHub, so the
sweeper is not — and cannot from here be — production-proven.

Three owner actions are required, listed at the end.

---

## Stage 0 — Checkout truth

| Field | Value |
|---|---|
| Working directory | `/Users/hmcherie/Desktop/CoinPilotX` |
| Branch | `release/full-sweep-20260826` |
| HEAD at start | `b2c2186a` |
| HEAD at end | `e27e0483` |
| `git status --short` | 65 entries (mixed: mission files, foreign concurrent work, untracked reports) |

**Concurrent foreign work — the directive's premise was partly false.** The
directive instructed me not to stage "Apple Pay / Marketplace files owned by
another session." I read every hunk of all nine modified tracked files before
staging. There is **no foreign Apple Pay work in the tracked diff**. The
concurrent Apple Pay session's output exists only as untracked markdown
(`APPLE_PAY_STAGE_0_1_AUDIT.md`, `APPLE_PAY_STAGE_A_I_VERIFICATION.md`), alongside
`UNDX_MARKET_BRIDGE_DEVICE_QA.md` and `docs/briefings/qa-artifacts/`. All nine
tracked modifications were mission-owned and were staged. Nothing foreign was
staged, and nothing foreign was overwritten.

## Stage 1 — Stale lock

Already clear on arrival: `.git/index.lock` did not exist and `pgrep -a git`
returned nothing. No action taken.

A **new** lock problem appeared later and is worth recording, because it will
recur: **this filesystem refuses `unlink()` on files under `.git`.** Git's
rename-into-place succeeds, so commits work — but git's own lock *cleanup* fails,
leaving stale locks that block the next ref update. Corroborating evidence: 1,913
stray `.git/objects/**/tmp_obj_*` files and 29 `.git/index.lock.*` junk files
dating to Jul 25. `rm -f` returns "Operation not permitted"; renaming the lock
aside (`mv .git/HEAD.lock .git/HEAD.lock.stale.<ts>`) works, and is evidently what
prior sessions did.

## Stage 2 — Explicit staging

No `git add -A`, no `git add .`, no `git commit -am`. Explicit paths only.

Midway, the shared `.git/index` was found to be carrying **a foreign session's
staged pile** — staging my 3 Stage-176 paths produced a 73-file staged diff
including UNDX reports, mobile-native i18n across 11 locales, briefings work and
`.env.example`. `git reset` would have destroyed that session's staging. Instead I
committed through a **private index** (`GIT_INDEX_FILE=/tmp/stage176.index` +
`git read-tree HEAD` + explicit `git add`), leaving `.git/index` untouched. The
index was backed up first to `/tmp/index.backup.1788224469`.

## Stage 3 — Commit

The wiring could not be committed standalone: `marketplace_reservation_{sweeper,
policy,reconciler}.py` were absent from HEAD *and* `origin/main`, and three
`cart.*` symbols the sweeper calls (`SETTLED_TRANSACTION_STATUSES`,
`note_reservation_deferral`, `settle_failed_transactions`) were absent on
`origin/main`. Committing only the wiring would have shipped an `ImportError` to a
production worker. Split into two ordered commits — the foundation is bootable on
its own because nothing imports the sweeper yet.

| SHA | Message | Files | Insertions | Deletions |
|---|---|---|---|---|
| `242ed3db` | `fix(marketplace): durable reservation lifecycle + one shared settlement path` | 17 | 3,748 | 54 |
| `e27e0483` | `fix(marketplace): wire durable reservation sweeper safely` | 3 | 1,564 | **0** |

`e27e0483` is **strictly additive** — zero deletions — which is the reviewability
property the directive asked for during a payment rollout.

## Stage 4 — Gates after commit

The working tree could not be trusted as a test substrate (see Stage 4b), so every
gate ran against a clean export of the committed tree:
`git archive e27e0483 | tar -x -C /tmp/verify176` (4,823 files).

| Gate | Result |
|---|---|
| 9-file reservation/payment gate set | **219 passed** |
| Full `tests/marketplace` | **249 passed** |
| Protected audio/live/call gate (`--base b2c2186a --head e27e0483`) | **PASS** — "No protected real-time audio path changed (20 file(s) inspected)", exit 0 |
| `marketplace_card_payments_paused()` | **True** |
| Shipped defaults | `sweep_enabled=False`, `sweep_dry_run=True`, interval clamp 60–300 s |

New failures: **0**.

### Stage 4b — Working-tree integrity incident (report to owner)

Twice during this session, files that had just been committed disappeared or
reverted in the working tree. Content was never at risk — it is safe in the two
commits — but **the working tree does not currently reflect HEAD**:

| File | Working-tree state |
|---|---|
| `bot.py` | **DIVERGED** — matches neither HEAD nor parent; a concurrent session is editing it |
| `services/marketplace_cart_routes.py` | reverted to parent `b2c2186a` |
| `services/marketplace_offers_routes.py` | reverted to parent |
| `tests/conftest.py` | reverted to parent |
| `scripts/stripe_webhook_recovery_audit.py` | reverted to parent |
| `pulse_worker.py`, `marketplace_reservation_{sweeper,policy,reconciler}.py` | = HEAD (correct) |

I deliberately did **not** overwrite `bot.py` — the directive forbids overwriting
concurrent work. Restoring the other four is a one-liner
(`git checkout HEAD -- <paths>`) but should be done when no other session is
mid-edit.

## Stage 5 — Merge to main

`git merge-tree --write-tree` is unsupported by the installed git (2.34.1), so the
conflict prediction was redone two ways: legacy three-argument `git merge-tree`,
and a **throwaway `--shared` clone** at `/tmp/mergeprobe` that touched no shared
refs and no shared worktree.

| Field | Value |
|---|---|
| Merge base | `b2c2186a` |
| Branch ahead of main by | 2 commits (`242ed3db`, `e27e0483`) |
| `origin/main` | `97b2b15a` — current, not stale (confirmed via HTTPS `git ls-remote`) |
| Main ahead of merge base by | 16 commits (SMII ops-center verification, MarketPulse route test, briefings fixes, Briefings hub + UNDX semantic retrieval) |
| **Merge result** | **CLEAN.** `Auto-merging bot.py / Automatic merge went well.` Zero unmerged paths, zero conflict markers. |
| Files the merge changes vs `97b2b15a` | 20 files, +5,312 / −54 |
| Only file changed on both sides | `bot.py` — auto-merged cleanly |
| Concurrent work preserved | Yes — nothing in the 16 main-side commits is touched or reverted |

**BLOCKER: the merge cannot be pushed.**

```
git push --dry-run origin HEAD:refs/heads/main
  → git@github.com: Permission denied (publickey).

git push --dry-run ro HEAD:refs/heads/main       (HTTPS remote)
  → fatal: could not read Username for 'https://github.com'
```

HTTPS *read* works, which is how `origin/main` was confirmed current. Write does
not. No SSH key, no HTTPS credential. This is the hard blocker.

## Stage 6 — Main gates (run against the merge result)

Because the merge is clean, I ran the Stage 6 gates against the merged tree anyway,
so the owner gets a pre-verified answer rather than an untested merge.

| Required gate | Result on merged tree |
|---|---|
| Payment pause TRUE | **TRUE** (`marketplace_payment_pause.marketplace_card_payments_paused()`) |
| Sweeper tests PASS | **PASS** (213 in the focused set) |
| Worker tests PASS | **PASS** (included above) |
| Wiring guard PASS | **PASS** |
| Full `tests/marketplace` | **249 passed** |
| Audio gate (`--base 97b2b15a --head <merge>`) | **PASS**, exit 0 |
| Symbol sanity on merged tree | `settle_failed_transactions` ✓, `run_reservation_sweep_if_due` ✓, `payment_intent.canceled` ×3 ✓, `marketplace_reservation_policy` ×9 ✓ |
| Shipped defaults on merged tree | `enabled=False`, `dry_run=True`, clamp 60–300 ✓ |

The probe clone was deleted afterwards. No refs, no branches, no remote state was
modified.

## Stages 7–10 — Deploy dry-run, deployed SHA, first production dry-run, three-cycle observation

**Not performed. Blocked by Stage 5.** Railway ground truth as of now:

| Field | Value |
|---|---|
| Project | `coinpilotx-alert-worker` — `111b3838-09d4-4f13-8b8b-6ed332bad06f` |
| Environment | `production` — `8bf01340-99d0-49be-a951-abffc17aa4d3` |
| Service | **`coinpilotx-pulse-worker`** — `094b83d0-6c69-4e0c-9230-a04b9f0d55f4` |
| Source | `hmcroody-alt/CoinPilotX`, branch **`main`** |
| Builder | NIXPACKS / V3 |
| Start command | `python pulse_worker.py` (per-service `deploy.startCommand`, overrides the Procfile — `pulse_worker` is **not** in the Procfile) |
| Latest deployment | `589ecc1c-1cbc-43e4-966b-71c3876a706d`, status **SUCCESS**, created 2026-09-01T01:00:09Z |
| Deployed SHA | **`97b2b15a`** — `origin/main`. **Does not contain the sweeper.** |
| `STRIPE_SECRET_KEY` | present |
| `MARKETPLACE_RESERVATION_SWEEPER_ENABLED` | **not set** |
| `MARKETPLACE_RESERVATION_SWEEPER_DRY_RUN` | **not set** |

Both flags absent means the sweep resolves **fail-closed OFF**. A deploy today
would not sweep anything even if the code were present. That is the intended
posture; do not read it as a misconfiguration.

Stage 9's dry-run counters (`scanned / candidates / would_release / would_defer /
would_skip / reconciled / failed`), oldest expired reservation, and total stranded
units are **unobtainable** until the code is on `main` and deployed.

## Stage 11 — Count truth (VERIFIED at runtime)

Executed against the merged tree with injected failures, not merely reasoned about:

| Case | `status` | `released` | `candidates` | Heartbeat `last_sweep_status` |
|---|---|---|---|---|
| Sweep ran, commit succeeded, **close failed** | `degraded` | **5** (preserved) | **7** (preserved) | `degraded` |
| **Commit failed** | `error` | **0** | 0 | `error` |

`DISTINGUISHABLE: True`. Monitoring cannot conflate the two. This matters because
`released` is precisely the number the dry-run→mutate GO/NO-GO decision reads —
reporting `status=error` with counts zeroed after a successful commit would
understate real mutation.

## Stage 12 — Stranded inventory decision

**Cannot be answered.** Requires a production dry-run, which requires deploy, which
requires push. Candidate transactions, stranded units, would-release / would-defer,
requires-Stripe-lookup and failed counts are all unknown. The directive's STOP
condition ("paid candidates: 0 REQUIRED / refunded candidates: 0 REQUIRED") has
not been evaluated against production data.

Note the code-level guard is in place regardless:
`SETTLED_TRANSACTION_STATUSES = ("paid", "refunded")` — settled orders are excluded
from release by the shared settlement path, and that exclusion is covered by the
committed tests.

## Stage 13 — No mutation yet

**HOLD RESPECTED.** `MARKETPLACE_RESERVATION_SWEEPER_DRY_RUN=false` has not been
set, and cannot be set until the owner reviews a production dry-run report that
does not yet exist. Neither sweeper variable was written to Railway in this
session. Marketplace card payments remain paused.

## Stage 14 — Pre-existing `pulse_worker` bug

**Kept out of this commit, as instructed.** The second `if conn:` in the feed-loop
teardown (now at `pulse_worker.py` line ~263) is pre-existing and untouched. The
sweep's own teardown uses the correct `if conn is not None:` — a DBAPI connection
may define `__bool__`/`__len__`, so a falsy-but-open handle would leak under the
bare truthiness test. A separate task should carry the feed-loop fix after Stage
176 rollout, to preserve this commit's strictly-additive review surface.

## Stage 15 — Stripe webhook owner blocker

**NOT VERIFIED — owner action.** `payment_intent.canceled` must be enabled on the
production Stripe webhook endpoint. `scripts/stripe_webhook_recovery_audit.py` now
lists it in `REQUIRED_EVENTS` and `bot.py` now has the handler branch
(previously missing entirely), but neither fact proves the Dashboard is configured.
Do not mark this verified until the Stripe Dashboard confirms it.

---

## What is proven

- Both commits are on `release/full-sweep-20260826`, fully gated: 219 / 249 passed, audio gate clean, payment pause TRUE.
- The merge onto `origin/main` (`97b2b15a`) is **clean**, including `bot.py`, and preserves all 16 intervening commits.
- All Stage 6 gates pass **on the merged tree**, not just on the branch.
- The sweep ships fail-closed: `enabled=False`, `dry_run=True`, interval clamped 60–300 s.
- Production currently has neither sweeper variable set, so nothing sweeps today.
- Degraded-vs-error count semantics verified by runtime fault injection.

## Owner actions required

1. **Provide push access** (SSH key or HTTPS credential) for `hmcroody-alt/CoinPilotX`, or merge `242ed3db` + `e27e0483` onto `main` yourself. The merge is verified clean — `git merge --no-ff e27e0483` from `main` needs no conflict resolution.
2. **Confirm `payment_intent.canceled` is enabled** on the production Stripe webhook endpoint (Stage 15).
3. **After deploy, review the production dry-run report** before anyone sets `MARKETPLACE_RESERVATION_SWEEPER_DRY_RUN=false` (Stage 13 hold point).

Minor, non-blocking: restore four working-tree files to HEAD when no concurrent
session is mid-edit (`services/marketplace_cart_routes.py`,
`services/marketplace_offers_routes.py`, `tests/conftest.py`,
`scripts/stripe_webhook_recovery_audit.py`), and leave `bot.py` alone until the
concurrent session editing it lands or abandons its work.
