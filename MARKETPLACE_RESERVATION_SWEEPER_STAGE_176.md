# Stage #176 — Wiring the Reservation Sweep into `coinpilotx-pulse-worker`

Built 2026-08-31 on branch `release/full-sweep-20260826` at `d37eccbf`.

---

## The finding that shapes this stage

**Railway builds `coinpilotx-pulse-worker` from `hmcroody-alt/CoinPilotX` branch
`main`. The reservation sweeper does not exist on `main`.**

```
Railway source          repo hmcroody-alt/CoinPilotX, branch main
local HEAD              release/full-sweep-20260826 @ d37eccbf
HEAD vs origin/main     15 ahead, 2 behind
origin/main:services/marketplace_reservation_sweeper.py   ABSENT
origin/main:services/marketplace_reservation_*            ABSENT (all four)
```

Every stage from 7 onward — the first dry-run cycle, the stranded-inventory
measurement, the GO/NO-GO, the mutation flip — is an observation of running
production code. None of that code is running. The Stage #175 service, the
policy module, the reconciler and the wiring built here are all on a release
branch that Railway has never deployed.

So stages 7 through 24 are not "not done"; they are **not yet performable**, and
the thing standing between them and execution is a branch integration plus a
deploy. Both are decisions with production consequence, and both are yours to
make rather than mine. Stages 0-6 and 21-27 — everything that is code, tests and
gates — are complete and are described below. Stages 7-24 are delivered as a
runbook with exact values, so that the moment the code is deployed the rollout
is a checklist rather than a redesign.

Reported this way rather than by flipping the Railway variables and calling it
observed, because `MARKETPLACE_RESERVATION_SWEEPER_ENABLED=true` on a worker
whose code has no sweeper would produce a completely clean log and a completely
false pass.

---

## Stage 0 — deployment truth

| Question | Answer | Source |
| --- | --- | --- |
| `pwd` | `/…/CoinPilotX` | shell |
| Branch | `release/full-sweep-20260826` | `git branch --show-current` |
| HEAD | `d37eccbf` | `git rev-parse --short HEAD` |
| Working tree | 13 modified, 17 untracked (concurrent Marketplace / Apple Pay work) | `git status --short` |
| Service | `coinpilotx-pulse-worker` `094b83d0-6c69-4e0c-9230-a04b9f0d55f4` | Railway |
| Project / env | `111b3838-…` / production `8bf01340-…` | Railway |
| **Start command** | **`python pulse_worker.py`** — read from the service's `deploy.startCommand`, not from the Procfile | `get-service-config` |
| Builder / replicas | NIXPACKS, 1 replica in `sfo` | `get-service-config` |
| `DATABASE_URL` | present | variable **names** only; no values read |
| `STRIPE_SECRET_KEY` | present | variable **names** only; no values read |
| `MARKETPLACE_*` variables | **none set** — the sweep will resolve entirely to code defaults | `get-service-config` |

The last row matters more than it looks. Because no marketplace variable exists
on the service today, whatever the code defaults are *is* what production will
do on the first deploy. That is why both flags default to the non-acting value.

---

## Stage 1 — the existing loop

`pulse_worker.py` before this change was 75 lines:

- `while True:` with `time.sleep(max(5, SLEEP_SECONDS))`, `SLEEP_SECONDS`
  defaulting to 20.
- One `try/except Exception` around the whole cycle, writing
  `bot.record_worker_heartbeat(WORKER_NAME, "healthy"|"error", …)`.
- Two jobs per cycle: `pulse_feed_engine.process_pending_jobs(12)` and
  `pulse_ai.run_due_space_ai_posts(…)`.
- `bot.db()` opened and closed inside the cycle, with `conn.commit()` and a
  `finally: conn.close()`.
- **No existing sub-interval jobs.** Every cycle did everything.

That last point is the reason Stage 2 is not optional. There was no precedent in
this worker for "run this less often", so the cadence had to be built rather than
joined. `pulse_ads_worker.py` already solves the same problem with a monotonic
`state` dict (`ATTRIBUTION_EVERY`, `BILLING_EVERY`, `REPORTING_EVERY`), so this
stage follows that in-repo pattern instead of inventing a third convention.

The feed loop's real period is `sleep + however long the feed took`, which is
variable — which is precisely why the sweep uses a monotonic deadline rather
than a cycle counter.

---

## Stages 2-6 — what was built

`pulse_worker.py`, 75 → 269 lines. Purely additive; no existing line changed
except the two insertion points.

**Stage 2 — one canonical cadence setting.**

```
MARKETPLACE_RESERVATION_SWEEP_SECONDS   default 300, clamped [60, 300]
```

Clamped rather than merely defaulted. Below a minute the sweep starts behaving
like the feed loop it was separated from; above five minutes stranded stock sits
longer than the 15-minute reservation TTL justifies. A missing zero cannot turn
a five-minute sweep into a five-second one — `test_13` pins all twelve boundary
cases. There is exactly one literal for the cadence and it is the constant.

**Stage 3 — two flags, both failing closed.**

```
MARKETPLACE_RESERVATION_SWEEPER_ENABLED   code default false
MARKETPLACE_RESERVATION_SWEEPER_DRY_RUN   code default true
```

`_env_flag` accepts `1/true/t/yes/y/on` and `0/false/f/no/n/off`, and returns the
default for everything else — unset, blank, `"maybe"`, `"TRUE_ISH"`, `"0.5"`.
The defaults are chosen so *unclear always resolves to the safe direction*: an
unparseable dry-run flag leaves the sweep in dry run. `test_10` sets both flags
to the same ten nonsense values simultaneously, because the dangerous
configuration is the pair — a value that reads as "on" for enabled and "off" for
dry-run is how a typo becomes released inventory.

Both are read at call time, not at import, so a Railway variable change takes
effect on the next sweep rather than requiring a redeploy. That is what makes
the Stage 17 mutation flip a variable change rather than a deployment.

**Stage 4 — the worker owns scheduling; the service owns decisions.**

The worker's entire interaction with the reservation subsystem is one call:

```python
summary = reservation_sweeper.run_reservation_expiry_sweep(
    cur, dry_run=dry_run, limit=reservation_sweeper.batch_limit(),
)
```

No SQL, no Stripe status interpretation, no release reasons. Three AST tests
check this: `test_15` parses the worker and rejects both the specific literals
(`update marketplace_listings`, `payment_intent`, `succeeded`,
`requires_action`, `reservation_expired`, …) and any bare SQL verb at all;
`test_16` rejects direct calls to `release_inventory_reservation`,
`settle_failed_transactions`, `note_reservation_deferral` and both `decide_*`
functions, in both attribute and bare-name call shapes; `test_17` asserts the
worker imports exactly one `marketplace_*` module and none of the forbidden
names. Docstrings are excluded from the literal scan by AST, so this file's own
prose naming what it forbids does not fail its own guard.

`test_17` is the one that guards the future rather than the present. Importing
the reconciler into the worker would not be a bug today, but it is the first
step of every drift: the import lands first, the direct call follows in a later
change.

**What these three tests are, and are not.** They are drift detection, not
proof. A static scan of a Python file cannot be exhaustive — `getattr`,
`importlib`, and a name assembled at runtime each defeat it by construction, and
no reachable amount of AST work closes that. What they do is make the *ordinary*
way this rule gets broken — someone adds an `UPDATE`, or reaches for
`settle_failed_transactions` because it is right there in the service next
door — fail loudly in CI instead of silently in production. An earlier draft of
this report described them as "enforced, not asserted". That was an
overstatement and is corrected here.

The limit was measured rather than assumed. An adversarial review of this stage
constructed four working evasions, two of which passed the tests as first
written: SQL whose table name is split across two literals (`"marketplace_" +
"listings"`), and `from services.cart import settle_failed_transactions`
followed by a bare-name call, which the original `test_16` could not see because
it read only `node.func.attr`. All four are now caught. The split literal is
caught by scanning for bare SQL verbs — a statement still needs its verb, and
the verb has nowhere to hide — and the bare-name call by collecting `ast.Name`
callees and by checking imported *names* as well as module names. `test_17` was
rescoped from the substring `reservation` to `marketplace`, because
`services.marketplace_cart_routes` — the module that owns every write to a
reservation row — contains no such substring and was the one settlement module
the original check waved through. The hardening was verified the same way it was
found: each evasion was appended to a parsed copy of the real `pulse_worker.py`
and the guards re-run. All four now report a hit, and the unmodified file
reports none — so the coverage was not bought with a false positive.

**Stage 5 — a sweep failure is an incident for the sweep only.**

The sweep is wrapped in its own `try/except` inside the cycle and returns a
structured `{"status": "error", …}` rather than propagating. The feed is this
worker's primary job and predates the sweep entirely; a marketplace bug taking
the feed offline would be strictly worse than the stranded inventory the sweep
exists to fix.

The sweep also takes **its own connection**. Sharing the feed's would mean a
failed sweep could roll back committed feed work.

**Stage 6 — monotonic, and no tight retry.**

```python
finally:
    state["reservation_sweep_due_at"] = time.monotonic() + interval
```

The deadline advances in `finally`, so a sweep that raises waits a full interval
instead of retrying on every 20-second cycle. Without that, a persistently
failing sweep would hit an already-unhealthy provider three times a minute,
which is how a small outage becomes a rate-limit incident. `test_09` runs
fifteen consecutive cycles against a sweep that always raises and asserts
exactly one attempt.

---

## Stage 21 — metrics, on the surface that already exists

Folded into `bot.record_worker_heartbeat` metadata rather than a new store:

```
reservation_sweep_enabled, reservation_sweep_interval,
last_sweep_at, last_sweep_status, last_sweep_dry_run,
last_sweep_candidates, last_sweep_released, last_sweep_would_release,
last_sweep_deferred, last_sweep_failed, last_sweep_needs_attention,
last_sweep_duration_ms
```

`last_sweep_status` is `ok`, `degraded` (the sweep ran but rows failed), or
`error` (the sweep itself raised) — so silence and failure do not look alike.

**A defect found and fixed while writing this.** The first implementation
reported only the current cycle's sweep. But the sweep runs on roughly one cycle
in fifteen, and `record_worker_heartbeat` does
`metadata_json=excluded.metadata_json` — a wholesale replace, not a merge. So
`last_sweep_at` would have been blanked on the fourteen cycles in between, and
an operator checking the heartbeat at an arbitrary moment during the dry-run
window would almost always have seen nothing — which reads identically to a
sweep that never ran, the one thing this rollout needs to distinguish. The last
outcome is now held in `state` and emitted on every heartbeat. `test_18` walks
fourteen declining cycles and asserts the fields survive all of them.

**A second defect, found by the adversarial review.** The `except` clause
originally discarded `summary` and reported `{"status": "error", …}` with every
count absent — correct when the sweep itself raised, wrong when the sweep ran,
committed, and then `conn.close()` failed. A dropped Postgres connection at
teardown would have reported `last_sweep_released: 0` for a cycle that had in
fact released stock, and `released` is precisely the number the Stage 16
GO/NO-GO decision reads. The cycle now tracks whether the commit succeeded: if
it did, the measured counts are preserved and the cycle is reported `degraded`
rather than `error`; if the commit is what failed, the transaction rolled back
and the optimistic summary is correctly discarded. `test_22` and `test_23` are
the two halves — a close failure must keep its counts, a commit failure must
never claim releases it did not make.

The heartbeat projection is also an allowlist rather than a passthrough:
`_sweep_metrics` reads a fixed set of keys out of the sweep summary instead of
splatting it in, so a field added to the service later cannot silently become
admin-visible. `test_20` now proves that direction by feeding the worker a sweep
summary carrying a buyer email, a card fingerprint and a live-looking key, and
asserting none survives. As first written that test asserted only that no secret
appeared in metadata that reads no environment at all — near-vacuous, and now
replaced.

---

## Stage 25 — the test matrix

`tests/marketplace/test_reservation_sweep_worker_wiring.py`, 23 test functions →
**52 tests**, covering the 11 required cases (the surplus is parametrised
expansion over flag spellings, interval boundaries and garbage inputs, plus the
connection-failure cases added after the adversarial review).

| # | Required case | Test | Result |
| --- | --- | --- | --- |
| 1 | Disabled → never calls sweeper | 01 — no call, **no connection opened** | PASS |
| 2 | Enabled + dry-run → calls with `dry_run=True` | 02 | PASS |
| 3 | Enabled + mutate → calls with `dry_run=False` | 03 | PASS |
| 4 | Interval respected | 05 — 15 cycles produce exactly 1 sweep | PASS |
| 5 | No 20-second hot loop | 04 — interval ≥ 15 × the feed loop's *source* default, and `MIN_SWEEP_SECONDS` ≥ 3 ×; 07 — monotonic, not `loop_count`, not wall clock | PASS |
| 6 | Sweeper exception does not kill worker | 08 — returns, does not raise; connection still closed, not committed | PASS |
| 7 | Second interval runs | 06 | PASS |
| 8 | Configuration parsing safe | 10 (10 garbage values, both flags at once), 11 (both flags, both directions), 13 (12 interval boundaries) | PASS |
| 9 | Default is non-mutating | 12 — unset ⇒ disabled, dry-run, 300s | PASS |
| 10 | Worker passes a bounded limit | 14 — bounded and env-tunable | PASS |
| 11 | No private settlement logic in worker | 15, 16, 17 — AST literal + bare-verb scan, call scan (attribute and bare name), import scan (modules and names) | PASS |
| + | Failing sweep does not spin | 09 — 15 cycles, 1 attempt | PASS |
| + | Metrics survive between sweeps | 18 | PASS |
| + | Health, failure and silence are three readings | 19 | PASS |
| + | Heartbeat is an allowlist, not a passthrough | 20 | PASS |
| + | Database that will not open is contained | 21 | PASS |
| + | Close failure keeps its release count | 22 | PASS |
| + | Commit failure claims no releases | 23 | PASS |

`test_04` reads the feed loop's default cadence out of the worker's source
rather than from `pulse_worker.SLEEP_SECONDS`. That constant is resolved from
the environment at import time, so a sandbox exporting
`PULSE_WORKER_SLEEP_SECONDS` would have made the case fail — or, worse, pass
vacuously — for reasons unrelated to the code it guards. This was the review's
finding; clearing the variable in the fixture would not have fixed it, since the
import has already happened by then.

The suite loads `pulse_worker` against a stubbed `bot`, restoring `sys.modules`
immediately so no other test module is affected by collection order. These tests
are about scheduling and authority; requiring a booted 111k-line Flask monolith
and a populated database would make them fail for reasons that have nothing to
do with the code they guard.

### Gate runs

```
test_reservation_sweep_worker_wiring.py                                   52 passed
test_reservation_sweeper.py                                               44 passed
test_reservation_settlement.py + test_reservation_webhook_wiring.py       50 passed
tests/test_marketplace_cash_payment_pause.py                               6 passed
tests/marketplace/ (full directory)                          249 passed, 0 failed
tests/marketplace/ + payment pause                           255 passed, 0 failed
```

The payment pause suite lives at `tests/test_marketplace_cash_payment_pause.py`,
not under `tests/marketplace/`, which is why it is invoked separately here.

**The full marketplace directory is now clean.** Stage #175 reported
`150 passed, 1 failed` plus three files failing to collect, all traced to
`services/feature_flag_engine.py` doing `from datetime import UTC` — correct
production code on the pinned Python 3.11, but absent on the 3.10 sandbox, and
because `bot.py` imports that module at scope it took the whole suite down at
collection. `tests/conftest.py` now restores `datetime.UTC` as the plain alias
of `timezone.utc` that 3.11 defines it to be, guarded on
`sys.version_info < (3, 11)` so it is a complete no-op on the interpreter
production runs and cannot mask a real defect there. That is a test-environment
repair, not a production change; `feature_flag_engine.py` is untouched.

---

## Stages 14, 23, 24, 26, 27

**Stage 14 — payment pause.** `marketplace_card_payments_paused()` evaluates
**TRUE** live, and `services/marketplace_payment_pause.py` is byte-identical to
`origin/main`. Untouched by this stage.

**Stage 23 — admin route.** There is no admin route that runs the sweep; the
worker is the only caller of `run_reservation_expiry_sweep` in the codebase.
Nothing to reconcile, and no second maintenance path exists to demote.

**Stage 24 — remains open, as instructed.** `payment_intent.canceled` is handled
in code (`bot.py:100802`), but whether the production Stripe endpoint is
*subscribed* to that event can only be confirmed in the Stripe Dashboard. Not
called verified.

**Stage 26 — protected-path gate.**

```
python3 scripts/realtime_audio_change_gate.py --base origin/main --head HEAD
→ No protected real-time audio path changed (19 files inspected). exit 0
```

**0 protected media changes.**

**Stage 27 — staging is blocked by a stale lock.** `.git/index.lock` is a
zero-byte file that neither `git` nor `rm` can unlink (`Operation not
permitted`) — the same mount permission condition seen previously on this
checkout. No git process is running. Nothing was committed, and none of
`git add -A`, `git add .`, `git reset --hard`, `git clean -fdx` was run at any
point. The concurrent Marketplace / Apple Pay work in the tree is untouched.

Once you clear the lock, the explicit staging for this stage is exactly:

```bash
rm -f .git/index.lock
git add pulse_worker.py \
        tests/conftest.py \
        tests/marketplace/test_reservation_sweep_worker_wiring.py \
        MARKETPLACE_RESERVATION_SWEEPER_STAGE_176.md
```

This stage's full diff is `pulse_worker.py +213 −0`, `tests/conftest.py +19`,
and one new test file. Nothing else. `git diff --stat pulse_worker.py` confirms
**zero deletions** — the wiring is strictly additive and no pre-existing line of
the feed loop was touched.

One consequence of holding that line: the feed loop's own connection teardown at
`pulse_worker.py:263` still reads `if conn:` rather than `if conn is not None`.
The review flagged it, and it is a real if minor latent bug — a DBAPI connection
that defines a falsy `__bool__` would be leaked. It is pre-existing on
`origin/main`, unrelated to the sweep, and changing it would cost the additive
property that makes this diff cheap to review during a payment-safety rollout.
The sweep's own teardown, which is new code, uses `is not None`. Worth a
separate one-line change; deliberately not smuggled into this one.

---

## The adversarial review

An independent review was run against the finished wiring before this report was
called done. Recording what it found, because a report that only lists what its
author already believed is not evidence of much.

It confirmed empirically: no exception escapes `run_reservation_sweep_if_due`
(tested for both `bot.db()` raising and `conn.close()` raising); the deadline
arithmetic is correct on every path including the empty-`state` first call; it
could not construct a pair of flag values that mutates unintentionally; and the
`conftest` shim is inert on 3.11.

It found five things worth fixing, all of which are fixed above: the two AST
evasions, the `released`-count loss on a teardown failure, `test_04`'s
dependence on an ambient environment variable, and three assertions weaker than
their names claimed (`test_11` never exercised `sweep_dry_run()`, `test_19`
checked only field presence, `test_20` could not have failed). It also noted
correctly that `pulse_worker` is absent from the `Procfile` — that is Stage 0's
finding rather than a defect: Railway's per-service `deploy.startCommand` for
`coinpilotx-pulse-worker` is `python pulse_worker.py`, read directly from
`get-service-config`, and it overrides the `Procfile` entirely. The directive's
"Do not trust Procfile" anticipated exactly this.

The test count moved 49 → 52 and the assertions inside several existing cases
got stronger, so the delta understates the change.

---

## Stages 7-24 — rollout runbook (blocked on deploy)

**Precondition: the reservation work must reach the branch Railway builds.**
Four service modules and their suites currently exist only on
`release/full-sweep-20260826`:

```
services/marketplace_reservation_policy.py
services/marketplace_reservation_reconciler.py
services/marketplace_reservation_sweeper.py
services/marketplace_cart_routes.py        (modified)
pulse_worker.py                            (modified)
```

**Step 1 — set the variables *before* the deploy**, so the first cycle of the
first deployment is already dry-run rather than briefly running on defaults:

| Variable | Value | Why |
| --- | --- | --- |
| `MARKETPLACE_RESERVATION_SWEEPER_ENABLED` | `true` | |
| `MARKETPLACE_RESERVATION_SWEEPER_DRY_RUN` | `true` | Stage 7 — first cycle must not mutate |
| `MARKETPLACE_RESERVATION_SWEEP_SECONDS` | `300` | matches the code default; set explicitly so it is visible |
| `MARKETPLACE_RESERVATION_SWEEP_BATCH` | leave unset | 50 is the intended first-run bound |

Setting `ENABLED=true` before deploy is safe precisely because `DRY_RUN`
defaults to true — even if the dry-run variable failed to apply, the code
default is still non-mutating.

**Step 2 — confirm the boot line.** The worker logs its configuration once at
startup:

```
RESERVATION_SWEEP_CONFIG enabled=True dry_run=True interval=300 batch=50 stripe_key_present=True
```

If `dry_run=False` appears here, stop and fix the variable before anything else.

**Step 3 — Stages 7-8, the measurement.** Each cycle logs
`RESERVATION_SWEEP_CYCLE` with the full 17-key summary. The stranded-inventory
answer is `would_release` — the count of reservations that *would* have had
stock returned. Because the dry-run path evaluates every decision and makes the
same provider calls while writing nothing, this is a real measurement rather
than an estimate.

**Step 4 — Stages 9-10, the NO-GO conditions.** Stop and do not proceed if the
dry run shows an unexpectedly high candidate count, reservations spanning weeks
or months, a large inventory volume, unknown states, or many Stripe lookup
failures. Absolutely NO-GO on any paid or refunded row eligible for release —
though note the service already excludes `t.status IN ('paid','refunded')` in
the candidate query *and* short-circuits locally-settled rows to `capture`
before any provider call, so a non-zero count there would indicate a defect, not
a policy decision.

**Step 5 — Stages 11-15.** Confirm provider failures appear as `deferred` with
`needs_attention`, never as `released` (`released` must stay 0 for the entire
dry-run window). Confirm `reconcile_deferrals` increments across cycles.
Restart the service mid-window and confirm the sweep resumes — the deadline is
in-memory by design, so a restart makes the next sweep immediate, never skipped.
Run **at least 3 successful cycles**; at a 5-minute cadence, 15-30 minutes.

**Step 6 — Stages 16-17, the flip.** If and only if all eight Stage 16
conditions hold, change **only** `MARKETPLACE_RESERVATION_SWEEPER_DRY_RUN` to
`false`. No redeploy is needed — the flag is read per sweep — and no unrelated
change should ride along.

**Step 7 — Stages 18-20.** On the first mutating cycle verify per released row:
reservation reached a terminal state, inventory restored exactly once, release
reason is `"expired"` (**not** `reservation_expired` — `RELEASE_REASONS` is a
closed frozenset and any unrecognised reason normalises to `manual`, which is
the outcome the directive forbids), transaction is neither paid nor refunded,
and telemetry is present. Then re-run the sweep and confirm it is a no-op: 0
duplicate restorations, 0 duplicate terminal downgrades.

**Step 8 — Stage 22, alerting.** Alert on `last_sweep_status=error`, on
`last_sweep_failed > 0`, and on `last_sweep_at` going stale by more than two
intervals. Do **not** alert on `last_sweep_candidates = 0` — that is the healthy
steady state.

---

## Final report

| Field | Result |
| --- | --- |
| Branch / HEAD | `release/full-sweep-20260826` / `d37eccbf` |
| Railway service | `coinpilotx-pulse-worker` `094b83d0-…` |
| Start command | `python pulse_worker.py` (service config, not Procfile) |
| Railway deploy branch | **`main` — does not contain the sweeper** |
| `DATABASE_URL` / `STRIPE_SECRET_KEY` | present / present (names only) |
| Feed loop cadence | 20 s (unchanged) |
| Sweep cadence | 300 s, clamped [60, 300], monotonic |
| Enabled flag | `MARKETPLACE_RESERVATION_SWEEPER_ENABLED`, code default **false** |
| Dry-run flag | `MARKETPLACE_RESERVATION_SWEEPER_DRY_RUN`, code default **true** |
| Settlement logic in worker | **none** — 3 AST drift guards (tests 15, 16, 17), 4 constructed evasions all caught |
| Worker → service call | `run_reservation_expiry_sweep` only |
| Sweeper exception kills worker | **no** (test 08) |
| Failing sweep hot-loops | **no** (test 09) |
| DB open / commit / close failure contained | **yes** (tests 21, 23, 22) |
| `released` count survives a teardown failure | **yes** (test 22) — defect found in review, fixed |
| Worker diff shape | **+213 −0** — strictly additive, no pre-existing line changed |
| Worker wiring tests | **52 passed** |
| Sweeper service tests | 44 passed |
| Settlement + wiring guard | 50 passed |
| Payment pause suite | 6 passed |
| Full `tests/marketplace/` | **249 passed, 0 failed** (was 150/1 + 3 collection errors) |
| Marketplace + payment pause | **255 passed, 0 failed** |
| Wiring guard — new private copies | **0** |
| `marketplace_card_payments_paused()` | **TRUE**, file untouched |
| Release reason contract | unchanged — `"expired"`; 0 occurrences of `reservation_expired` outside a test's forbidden-literal list |
| Protected media changes | **0** (audio gate exit 0, 19 files inspected) |
| Forbidden git commands used | **none** |
| Committed | no — `.git/index.lock` unremovable |
| Deployed | **no** |
| First production cycle observed | **not performed** — code is not on the deploy branch |
| Stranded inventory measured | **not performed** — same reason |
| Mutation enabled | **no** |
| Stage 24 Stripe subscription | **open** — requires Dashboard confirmation |

### FINAL VERDICT: STAGE #176 PARTIAL

Everything that is code is complete and gated: the wiring, the independent
cadence, the fail-closed flags, the failure containment, the metrics, 52 wiring
tests, a fully green marketplace suite, a clean audio gate and an untouched
payment pause. The worker will do nothing at all until two variables are
explicitly set, and will not mutate until a third is explicitly cleared. Two
defects found by adversarial review — a release count lost on a teardown
failure, and two AST guards that could be evaded — are fixed and covered, and
one overstated claim in this report has been corrected.

What is not done is the live rollout — stages 7 through 20 — and it is not done
because the sweeper is not on the branch Railway deploys. Calling that portion
PASS would mean reporting an observation I did not make. The runbook above turns
it into a checklist the moment you decide to integrate and deploy.
