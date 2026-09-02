# Marketplace Reservation Sweeper — Production Dry-Run Acceptance Report

**Directive:** PRODUCTION DRY-RUN ACCEPTANCE ONLY
**Date:** 2026-08-31
**Mutation authorized:** NO — dry-run only, no reservation mutation performed
**FINAL VERDICT: NO-GO**

---

## 0. Headline — the sweeper is non-functional in production

The deploy is correct, the variables are correct, the cadence is correct, the failure
containment is correct, and nothing was mutated. But the sweep itself never runs. Every
cycle dies inside the candidate query:

```
psycopg2.errors.UndefinedColumn: column r.expires_at does not exist
LINE 1: ...listing_id AS listing_id, r.quantity AS quantity, r.expires_...
```

Three consecutive production cycles returned `failed: 1` with every other counter at `0`.
The inventory leak that Stage 176 exists to fix is not being addressed in production, and
the dry-run numbers this directive asks for cannot be measured because the query that
would produce them never executes.

### Root cause

`marketplace_inventory_reservations` in production PostgreSQL is missing the seven
lifecycle columns:

```
reserved_at, expires_at, released_at, captured_at,
release_reason, reconciled_at, reconcile_deferrals
```

Those columns are added by `_ensure_reservation_lifecycle_columns(cur)`, which is called
from exactly one place in the codebase — `services/marketplace_cart_routes.py:207`, inside
`_ensure_schema`, which is itself guarded by a per-process `_SCHEMA_READY` global and is
only invoked from cart route handlers on the **web** service.

`services/marketplace_reservation_sweeper.py` imports `marketplace_cart_routes as cart`
(line 60) but never calls `cart._ensure_schema(cur)`. The worker process therefore queries
columns that its own process has never migrated into existence.

The `except Exception` fallback in `select_expiry_candidates` (line ~228) does not save it.
The fallback was written on the assumption that only the *newer* columns
(`reconciled_at`, `reconcile_deferrals`) could be absent, so it still selects
`r.expires_at` — and fails identically. Its own comment states the stakes exactly:

> "a sweeper that stops working after a partial migration recreates the leak it exists to fix"

That is precisely what is happening.

### Remedy (not applied — outside this directive's scope)

1. `services/marketplace_reservation_sweeper.py` must ensure its own schema: call
   `cart._ensure_schema(cur)` (or `cart._ensure_reservation_lifecycle_columns(cur)`)
   before `select_expiry_candidates`. A worker cannot depend on a lazy migration that only
   fires in a different process on a different service.
2. The fallback query at line ~228 must stop assuming `expires_at` exists. If the column is
   genuinely absent it should degrade to a no-op with an explicit `needs_attention` signal,
   not raise.

Both are small, additive changes. Neither is authorized here.

---

## 1. Railway deploy verification

Verified from Railway deployment metadata **and** from the worker's own boot log — not
inferred from GitHub.

| Field | Value |
|---|---|
| Project | `coinpilotx-alert-worker` (`111b3838-09d4-4f13-8b8b-6ed332bad06f`) |
| Environment | `production` (`8bf01340-99d0-49be-a951-abffc17aa4d3`) |
| Service | `coinpilotx-pulse-worker` (`094b83d0-6c69-4e0c-9230-a04b9f0d55f4`) |
| Deployment | `62505234-190e-4fef-8d3f-24622378a161` — **SUCCESS** |
| Deployed SHA | `8a21d1b9b98b160cb6d97ac30e23e39cd264ec6a` |
| Worker boot log | `PULSE_WORKER_BOOT sha=8a21d1b9b98b` |
| Source | `hmcroody-alt/CoinPilotX`, branch `main` |
| Builder | NIXPACKS / V3 |
| Start command | `python pulse_worker.py` (per-service override; `pulse_worker` is not in the Procfile) |
| Region / replicas | sfo / 1 |
| Created | 01:23:03Z |
| Container start | 01:25:52Z |

Prior deployment `d6e48d7c` also SUCCESS at the same SHA.

Ancestry confirmed locally against `8a21d1b9`: `242ed3db` present, `e27e0483` present,
`7eac699d` present. All four sweeper files present in that tree.

The web service `CoinPilotX` (`ce41f7c5-b882-4aa7-81b3-06de73fded31`) is also deployed at
`8a21d1b9` — so the migration code exists in production; it simply had not executed as of
the last observed cycle.

**RESULT: PASS**

---

## 2. Variables

Both sweeper variables were **absent** and were set to exactly the required values. No
other variable was changed. `DRY_RUN` was not set to false.

| Variable | Value |
|---|---|
| `MARKETPLACE_RESERVATION_SWEEPER_ENABLED` | `true` |
| `MARKETPLACE_RESERVATION_SWEEPER_DRY_RUN` | `true` |

Effect confirmed by the post-redeploy boot line — it read `enabled=False` before the change:

```
RESERVATION_SWEEP_CONFIG enabled=True dry_run=True interval=300 batch=50 stripe_key_present=True
```

Presence-only checks (values never read or logged):

| Variable | Worker | Web service |
|---|---|---|
| `DATABASE_URL` | present | present |
| `STRIPE_SECRET_KEY` | present | present |
| `STRIPE_WEBHOOK_SECRET` | **absent** | present |

`STRIPE_WEBHOOK_SECRET` missing on the worker is benign: the worker does not receive
webhooks. The web service, which does, has it. The worker's boot-time
"STRIPE_WEBHOOK_SECRET is missing" line is noise in this context.

**RESULT: PASS**

---

## 3. Payment pause

`services/marketplace_payment_pause.py:49` on the deployed blob:

```python
def marketplace_card_payments_paused() -> bool:
    return True
```

Hardcoded — no environment dependency, so TRUE unconditionally on the deployed SHA. No
checkout unpause was performed or attempted.

**RESULT: TRUE — PASS**

---

## 4. First dry-run cycle

Cycle 1 at **01:25:52** (duration_ms 6):

| Metric | Value |
|---|---|
| scanned | 0 |
| candidates | 0 |
| would_release (transactions) | 0 |
| would_release (units) | 0 |
| would_defer | 0 |
| would_skip | 0 |
| reconciled | 0 |
| failed | **1** |
| oldest expired reservation | **UNMEASURABLE** — query never executed |

No reservation was mutated. `dry_run=True` throughout.

These zeros are **not** a clean result. They are the shape of a query that raised before
touching a row.

**RESULT: FAIL — sweep did not execute**

---

## 5. Three cycles observed

| Cycle | Time | Interval | duration_ms | Result |
|---|---|---|---|---|
| 1 | 01:25:52 | — (boot) | 6 | `failed: 1` |
| 2 | 01:30:52 | 300 s | 17 | `failed: 1` |
| 3 | 01:35:53 | 301 s | 5 | `failed: 1` |

All three returned identically:

```python
{'scanned': 0, 'candidates': 0, 'released': 0, 'captured': 0, 'deferred': 0,
 'skipped': 0, 'reconciled': 0, 'failed': 1, 'would_release': 0, 'would_defer': 0,
 'would_skip': 0, 'provider_calls': 0, 'needs_attention': 0, 'dry_run': True,
 'limit': 50, 'batch_exhausted': False}
```

- Maintenance interval respected: 300 s / 301 s against a 300 s target.
- **Not** running on the 20-second feed cadence — sub-interval scheduling is working.
- Worker loop healthy: no `PULSE_WORKER_CYCLE_FAILED` in logs across the observation window.
- No repeated crash — the worker did not restart.
- `dry_run` remained `True` on every cycle.
- Stripe reconciliation: **not exercised.** Zero candidates reached the reconciler, so this
  path is untested in production. Reporting it as PASS would be false.

The failure containment worked exactly as designed: the sweep raised, was caught, was
logged, wrote a terminal `status` into `state`, set the next deadline in `finally`, and the
feed loop continued untouched. The containment is correct. The thing it is containing is
not.

**RESULT: cadence PASS / sweep FAIL**

---

## 6. Zero-tolerance safety check

| Check | Value |
|---|---|
| paid release candidates | 0 |
| refunded release candidates | 0 |

**These are vacuously 0.** Zero rows were scanned, so zero rows could be classified. This
is not evidence that the `SETTLED_TRANSACTION_STATUSES` guard works in production — it is
evidence that nothing reached the guard. The safety property is untested against real data
and must not be recorded as passed.

The guard is verified by the unit suite (`tests/marketplace`, 249 passed) and by the query
predicate `AND (t.status IS NULL OR t.status NOT IN (...))`, but source-level and test-level
verification is not production verification.

**RESULT: UNVERIFIED (vacuously 0) — not a pass**

---

## 7. Stranded inventory report

| Metric | Value |
|---|---|
| candidate transactions | UNMEASURABLE |
| stranded units | UNMEASURABLE |
| would release (transactions) | UNMEASURABLE |
| would release (units) | UNMEASURABLE |
| would defer | UNMEASURABLE |
| would reconcile | UNMEASURABLE |
| failed | 1 per cycle |
| oldest candidate | UNMEASURABLE |

The whole point of the dry run was to produce this table. It cannot be produced until the
schema defect is fixed. Note that the underlying stranded inventory is real and unaffected
by this — the reservations are still sitting in the database in `held`. We simply cannot
count them yet.

**RESULT: BLOCKED**

---

## 8. Count-truth regression

Verified by runtime fault injection on the merged tree (not by inspection):

| Scenario | status | released | candidates |
|---|---|---|---|
| commit succeeds, close fails | `degraded` | 5 (preserved) | 7 |
| commit fails | `error` | 0 | — |

`DISTINGUISHABLE: True`. The two failure modes are not conflated. A post-commit teardown
failure preserves the true released count and reports `degraded`; a commit failure reports
`error` with `released=0`, correctly refusing to claim work that was rolled back.

**RESULT: PASS**

---

## 9. Restart / redeploy durability

Satisfied by the observed redeploy at 01:23:03Z → container start 01:25:52Z.

- Worker resumed cleanly.
- Schedule resumed; the sweep fired on the first cycle after boot.
- `dry_run` remained `True` across the restart.
- Reservation expiry state is **durable** — it lives in the database `expires_at` column,
  not in process memory.

One honest caveat: `state["reservation_sweep_due_at"]` is **in-memory only**. A restart
resets the sub-interval deadline, so the worker performs one immediate extra sweep after
every boot instead of honoring the remaining time from the previous process. This is
harmless at a 300 s interval and in dry-run mode, but it means restart frequency is an
input to sweep frequency. Worth knowing before mutation is enabled.

**RESULT: PASS (with the in-memory-deadline caveat noted)**

---

## 10. Stripe webhook — `payment_intent.canceled`

Not verifiable from this environment. Confirming which events the production Stripe webhook
endpoint subscribes to requires reading the endpoint configuration in the Stripe Dashboard
or via the Stripe API with a live key — neither is available here, and the directive
explicitly forbids calling this PASS from source alone.

`STRIPE_WEBHOOK_SECRET` is present on the web service, so an endpoint is configured. Whether
`payment_intent.canceled` is among its enabled events is unknown.

**RESULT: OWNER ACTION REQUIRED** — check Stripe Dashboard → Developers → Webhooks → the
production endpoint → Events, and confirm `payment_intent.canceled` is subscribed.

---

## 11. Stop point

`MARKETPLACE_RESERVATION_SWEEPER_DRY_RUN` remains `true`. It was not changed and will not be
changed. Mutation mode is OFF. This report is returned first, as instructed.

---

## FINAL REPORT

| Field | Result |
|---|---|
| MAIN SHA | `8a21d1b9b98b160cb6d97ac30e23e39cd264ec6a` |
| RAILWAY WORKER SHA | `8a21d1b9b98b160cb6d97ac30e23e39cd264ec6a` (deployment `62505234`, verified from Railway metadata + worker boot log) |
| DEPLOY | PASS — SUCCESS, start command `python pulse_worker.py`, worker running |
| SWEEPER ENABLED | PASS — `true` (was absent; set this session) |
| DRY RUN | `true` — PASS, unchanged, verified on all 3 cycles |
| DATABASE | PASS — `DATABASE_URL` present |
| STRIPE | PASS — `STRIPE_SECRET_KEY` on worker; `STRIPE_WEBHOOK_SECRET` on web service |
| CYCLE 1 | 01:25:52 — `failed=1, scanned=0, candidates=0` (6 ms) |
| CYCLE 2 | 01:30:52 — `failed=1, scanned=0, candidates=0` (17 ms) |
| CYCLE 3 | 01:35:53 — `failed=1, scanned=0, candidates=0` (5 ms) |
| CANDIDATES | 0 — **unmeasurable**, query never executed |
| WOULD RELEASE | 0 transactions / 0 units — unmeasurable |
| WOULD DEFER | 0 — unmeasurable |
| RECONCILED | 0 — reconciler never reached |
| FAILED | 1 per cycle, 3/3 cycles |
| PAID RELEASE CANDIDATES | 0 — **vacuous**, not a passed safety check |
| REFUNDED RELEASE CANDIDATES | 0 — **vacuous**, not a passed safety check |
| WORKER CADENCE | PASS — 300 s / 301 s; no 20-second hot loop |
| RESTART DURABILITY | PASS — expiry durable in DB; sweep deadline in-memory only |
| PAYMENT PAUSE | TRUE — PASS, hardcoded, no unpause attempted |
| `payment_intent.canceled` | OWNER ACTION REQUIRED — not verifiable from this environment |
| MUTATION MODE | OFF |
| **FINAL VERDICT** | **NO-GO** |

### Why NO-GO

The infrastructure passed every check. The sweeper did not run even once. Enabling mutation
against a query that raises before it selects a row would accomplish nothing and would
remove the dry-run guard for no gain. The schema defect must be fixed and a clean dry-run
cycle observed — with real, nonzero `scanned` and a genuine zero on paid/refunded release
candidates — before mutation can be considered.

### Blocking item

`services/marketplace_reservation_sweeper.py` does not ensure its own schema. Fix that, plus
the fallback query's `expires_at` assumption, redeploy, and re-run this acceptance.
