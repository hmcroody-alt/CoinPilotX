# Stage 176B — Worker Schema Bootstrap + Migration-Safe Fallback

**Date:** 2026-08-31
**Commit:** `3b431221a8251ff4542fe4e8488290da4453e61d`
**Parent:** `8a21d1b9b98b160cb6d97ac30e23e39cd264ec6a`
**Scope:** 7 files, +1351 / −114

---

## FINAL VERDICT: NO-GO — code fix complete and verified, production measurement blocked

The engineering objective of Stage 176B is met. Every stage from 1 to 15 is
closed with evidence. Stages 16 through 19 cannot be executed by me because the
push to `origin` requires a credential I do not hold, and the Railway service
deploys from GitHub `main`. The verdict is NO-GO not because anything failed but
because Stage 18's zero-tolerance check is defined as a measurement on a
*functioning* production scan, and no such scan exists yet. Recording a PASS
without it would be exactly the vacuous PASS the directive forbids.

---

## Root cause

Confirmed as stated, and then measured rather than assumed.

The lifecycle columns the sweep reads — `expires_at` above all — were created
only by `marketplace_cart_routes._ensure_schema`, which is reachable from a cart
route handler and nowhere else. `pulse_worker` runs as its own Railway service
and never serves an HTTP request, so the dependency read: *a buyer opens a cart →
the columns exist → the sweeper works*. Until a buyer did, every cycle failed.

The measurement added two facts the directive's diagnosis implied but did not
state. First, the fallback never fell back: `select_expiry_candidates` line 221
and line 228 both select `r.expires_at`, so both raise the same
`UndefinedColumn`. Second, the outer handler swallowed both and returned
`{'scanned': 0, 'candidates': 0, 'released': 0, …, 'failed': 1}` with **no
status and no reason key anywhere in it**. The old sweep did not crash. It
reported a shape indistinguishable from a healthy sweep of an empty queue. An
inventory leak and a clean bill of health were the same three numbers.

Production log from the currently deployed `8a21d1b9`, one of five consecutive
cycles at five-minute spacing:

```
01:55:54 ERROR RESERVATION_SWEEP_CANDIDATE_QUERY_FALLBACK
         File "/app/services/marketplace_reservation_sweeper.py", line 221
01:55:54 ERROR RESERVATION_SWEEP_CANDIDATE_QUERY_FAILED
         File "/app/services/marketplace_reservation_sweeper.py", line 228
01:55:54 INFO  RESERVATION_SWEEP_CYCLE dry_run=True interval=300
         summary={'scanned': 0, 'candidates': 0, 'released': 0, 'failed': 1, …}
```

---

## Verified fields

| Field | Result |
|---|---|
| **ROOT CAUSE** | Lifecycle DDL owned by a route module; worker process never reaches it. Confirmed by production log and reproduced locally. |
| **SCHEMA OWNER** | `services/marketplace_reservation_schema.py` — new, canonical, single copy of the DDL. |
| **SCHEMA ENSURE** | `ensure_reservation_schema(cur, *, force=False)`. Idempotent, non-destructive, never raises, returns `ready` / `missing` / `error` as data. |
| **WORKER INDEPENDENT OF WEB TRAFFIC** | YES. Sweeper calls the shared ensure before its first candidate query. No route import for schema mutation. |
| **FRESH DB TEST** | PASS — `test_01_sweep_bootstraps_its_own_schema_with_no_cart_request`, plus the portable `test_the_worker_bootstraps_the_schema_without_any_web_request`. |
| **PARTIAL MIGRATION TESTS** | 10/10 PASS — tests 06–15, one per directive case. |
| **FALLBACK NO LONGER REFERENCES MISSING COLUMNS** | YES. `_candidate_projection` builds SQL from the live table shape; `test_04_the_generated_sql_never_names_an_absent_column` asserts it against recorded statements. |
| **BLOCKED SWEEP REPORTING** | `status=degraded`, `failed=1`, `reason=schema_missing`. Never `candidates=0, status=ok`. |
| **WORKER SURVIVES SCHEMA FAILURE** | YES — tests 16, 17, 18. Loop stays alive; the failed ensure is not cached, so the next interval retries. |
| **DDL CONCURRENCY** | Race-safe — tests 20, 21, 22. Losing an `ADD COLUMN` race is a normal outcome, not an error. |
| **OBSERVABILITY** | `reservation_schema_ready` / `schema_missing` / `schema_ensure_failed` are distinct. `last_sweep_reason` now reaches the heartbeat, separating "this database needs a migration" from "a row misbehaved" — previously both arrived as `degraded, failed=1`. |
| **COUNT SEMANTICS** | Unchanged. Commit-succeeded-then-close-failed stays `degraded` with `released` preserved; commit failure stays `error` with `released=0`. Tests 25, 26 and wiring test 24. |
| **TESTS** | `tests/marketplace` **281 passed** (baseline 249; +26 bootstrap, +4 contract, +2 wiring). |
| **REGRESSION GATE** | `tests/protection` 3 failed / 145 passed — **identical on the `8a21d1b9` baseline**. Pre-existing Agora `KeyError: 'can_publish'`, unrelated. **0 new failures.** |
| **NON-VACUITY** | Byte-identical contract file: **4 passed** on the fixed tree, **2 failed** on `8a21d1b9` with `sqlite3.OperationalError: no such column: r.expires_at`. Proven behaviourally, not by import error. |
| **PROTECTED AUDIO GATE** | PASS — 7 files inspected, no protected real-time audio path changed. |
| **MAIN SHA** | `3b431221a8251ff4542fe4e8488290da4453e61d` (local `main`, fast-forwarded from `97b2b15a`; no force push, no merge commit, tree identical to the verified worktree). |
| **MUTATION MODE** | **OFF.** `marketplace_card_payments_paused()` = `True`. `sweep_dry_run()` defaults `True`; `sweep_enabled()` defaults `False`. No mutation path changed by this commit. |

---

## Blocked fields

| Field | Status |
|---|---|
| **PUSHED TO ORIGIN** | **BLOCKED** — `git@github.com: Permission denied (publickey)`. |
| **RAILWAY SHA** | `8a21d1b9b98b12` — still the broken tree. Service `coinpilotx-pulse-worker` deploys from `hmcroody-alt/CoinPilotX` branch `main`; it cannot pick up `3b431221` until the push lands. |
| **DRY RUN (live)** | `enabled=True dry_run=True interval=300 batch=50 stripe_key_present=True` — correct and unchanged. |
| **CYCLE 1 / 2 / 3** | **NOT MEASURED** — would measure the broken build. |
| **FAILED (0 required)** | **NOT MEASURED.** Currently `failed=1` on every live cycle. |
| **CANDIDATES / WOULD RELEASE / WOULD DEFER / RECONCILED** | **NOT MEASURED.** |
| **PAID RELEASE CANDIDATES (0 required)** | **NOT ASSESSED.** A zero here today would be vacuous — the scan cannot see the table. |
| **REFUNDED RELEASE CANDIDATES (0 required)** | **NOT ASSESSED**, same reason. |

Per Stage 19, `DRY_RUN` has **not** been set to false and must not be until
three clean cycles are measured and returned.

---

## Handoff — one action needed

From a shell with push rights to `hmcroody-alt/CoinPilotX`:

```bash
cd /Users/hmcherie/Desktop/CoinPilotX
git push origin 3b431221a8251ff4542fe4e8488290da4453e61d:main
```

This is a fast-forward from `origin/main` at `8a21d1b9`. No force push, no merge
commit, no branch rewrite. Railway auto-deploys `main`; keep `ENABLED=true` and
`DRY_RUN=true`. Once `PULSE_WORKER_BOOT sha=3b431221` appears in the logs, three
sweep cycles at five-minute spacing supply every blocked field above.

Note on the worktree: git operations here leave `index.lock` and `HEAD.lock`
behind because this mount permits `rename` but denies `unlink`. If a git command
reports the lock exists, `mv` it aside rather than `rm` it.
