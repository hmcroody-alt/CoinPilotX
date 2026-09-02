# Stage 176B — Production Deployment + DRY-RUN Acceptance

**Date:** 2026-09-01 (UTC)
**Commit under test:** `3b431221a8251ff4542fe4e8488290da4453e61d`
**Previous production commit:** `8a21d1b9b98b160cb6d97ac30e23e39cd264ec6a`
**Service:** `coinpilotx-pulse-worker` (Railway, project `111b3838`, env `production`)

Supersedes `STAGE_176B_SCHEMA_BOOTSTRAP_REPORT.md`, whose NO-GO was recorded
solely because the push had not landed. The push landed; the measurement it was
waiting for now exists.

---

## FINAL VERDICT: DRY-RUN PASS — with one scope limit stated plainly below

Every gate the directive defines as measurable was measured on the live
production worker against the live production database, and every one passed.
The scope limit is not a failure of the fix; it is a fact about the data the fix
now has access to, and it is recorded in full rather than buried.

---

## Deployment

| Field | Result |
|---|---|
| **MAIN SHA** | `3b431221a8251ff4542fe4e8488290da4453e61d`. Read from the remote, not from local state: `git ls-remote ro refs/heads/main` → `3b431221a8251ff4542fe4e8488290da4453e61d`. Fast-forward descendant of `8a21d1b9`; no force push, no merge commit. |
| **RAILWAY SHA** | `3b431221a8251ff4542fe4e8488290da4453e61d`. Deployment `108ef211-7696-4cc4-89aa-9c73dc663e56`, `status=SUCCESS`, `commitHash=3b431221a8251ff4542fe4e8488290da4453e61d`. Prior deployment `62505234` moved to `REMOVED`. |
| **DEPLOY** | **SUCCESS — proven at the runtime layer, not inferred from GitHub.** Three independent levels agree: the remote ref, the Railway deployment record, and the process's own first log line: `02:25:17 INFO PULSE_WORKER_BOOT sha=3b431221a825`. The worker itself reports the SHA it is executing. |

---

## Schema bootstrap — the root-cause fix, observed in production

The single most important line in this report:

```
2026-09-01 02:25:17,822 INFO RESERVATION_SCHEMA_READY
  table=marketplace_inventory_reservations columns=15
  added=reserved_at,expires_at,released_at,captured_at,release_reason,reconciled_at,reconcile_deferrals
  optional_present=reconciled_at,reconcile_deferrals
```

All **seven** lifecycle columns appear in `added=`. That is the proof the
directive's diagnosis was correct and that the fix works: the production
database genuinely did not have these columns, and the *worker* created them —
100 ms after boot, in its own process, with no HTTP request of any kind having
been served, because this service never serves one.

| Field | Result |
|---|---|
| **SCHEMA BOOTSTRAP** | **SUCCESS.** Seven columns created by the worker on first boot. |
| **SCHEMA READY SIGNAL** | **EXPLICIT AND PRESENT** — `RESERVATION_SCHEMA_READY … columns=15`. A distinct event, not an absence of errors. The blocked path emits `RESERVATION_SWEEP_SCHEMA_BLOCKED` instead; the two can never be confused. |
| **WORKER INDEPENDENT OF WEB TRAFFIC** | **YES — demonstrated, not argued.** `coinpilotx-pulse-worker` runs `python pulse_worker.py`, binds no port, and serves no route. The columns were absent at 02:25:17.7 and present at 02:25:17.8. Nothing but the worker could have created them. |
| **UndefinedColumn ELIMINATED** | **YES.** Zero occurrences of `RESERVATION_SWEEP_CANDIDATE_QUERY_FALLBACK`, `RESERVATION_SWEEP_CANDIDATE_QUERY_FAILED`, or `psycopg2.errors.UndefinedColumn` across the entire post-deploy log window. On `8a21d1b9` both error lines appeared on every single cycle. |

---

## Cycles

Four cycles observed; three required.

| # | Timestamp (UTC) | status | reason | scanned | candidates | would_release (tx) | would_release (units) | would_defer | reconciled | failed |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 02:25:17.838 | `ok` | `None` | 0 | 0 | 0 | 0 | 0 | 0 | **0** |
| 2 | 02:30:18.522 | `ok` | `None` | 0 | 0 | 0 | 0 | 0 | 0 | **0** |
| 3 | 02:35:19.210 | `ok` | `None` | 0 | 0 | 0 | 0 | 0 | 0 | **0** |
| 4 (post-restart) | 02:36:11.555 | `ok` | `None` | 0 | 0 | 0 | 0 | 0 | 0 | **0** |

Cycle 1 verbatim:

```
02:25:17 INFO RESERVATION_SWEEP_STARTED candidates=0 limit=50 dry_run=True
02:25:17 INFO RESERVATION_SWEEP_COMPLETED candidates=0 released=0 captured=0
         deferred=0 skipped=0 failed=0 provider_calls=0 attention=0
         dry_run=True duration_ms=54
02:25:17 INFO RESERVATION_SWEEP_CYCLE dry_run=True interval=300
         summary={'status': 'ok', 'reason': None, 'scanned': 0, 'candidates': 0,
         'released': 0, 'captured': 0, 'deferred': 0, 'skipped': 0,
         'reconciled': 0, 'failed': 0, 'would_release': 0, 'would_defer': 0,
         'would_skip': 0, 'provider_calls': 0, 'needs_attention': 0,
         'dry_run': True, 'limit': 50, 'batch_exhausted': False,
         'duration_ms': 54}
```

| Field | Result |
|---|---|
| **FAILED (0 REQUIRED)** | **0 on every cycle. PASS.** The old build reported `failed=1` on all five cycles sampled before the fix. |
| **SCANNED** | 0 |
| **CANDIDATES** | 0 |
| **WOULD RELEASE (transactions / units)** | 0 / 0 |
| **WOULD DEFER** | 0 |
| **RECONCILED** | 0 |
| **CADENCE** | **~300 s, no hot loop. PASS.** Cycle 1→2 = 300.684 s, cycle 2→3 = 300.688 s, against a configured `interval=300`. Drift under 0.7 s per cycle. `duration_ms` was 54, 10, 10 — the worker is idle between cycles, not spinning. |
| **RESTART DURABILITY** | **PASS, and it doubles as the idempotency proof.** After an in-place restart the worker re-booted at `sha=3b431221a825` and emitted `RESERVATION_SCHEMA_READY … columns=15 added=- optional_present=reconciled_at,reconcile_deferrals`. `added=-` — empty. The second run found all seven columns already there, added none, still reported ready, still scanned cleanly with `failed=0`. Bootstrap is idempotent and non-destructive across process lifetimes. |

---

## Non-vacuity of the zero — read this section before acting on the numbers

The directive is explicit that `scanned=0 + failed=1` must never be read as an
empty queue. It is not being read that way here. The evidence that the SELECT
genuinely executed against the real schema and genuinely returned zero rows:

1. `RESERVATION_SCHEMA_READY … columns=15` — the table was introspected and all
   five required sweep columns were confirmed present before any query ran.
2. `RESERVATION_SWEEP_STARTED` is emitted *after* the candidate query returns; it
   carries the row count. Its presence means the query completed.
3. No `CANDIDATE_QUERY_FAILED` and no `CANDIDATE_QUERY_FALLBACK` anywhere.
4. `status='ok'`, `reason=None`, `failed=0` — the sweep's own structured verdict,
   distinct from the pre-fix shape which carried no `status` or `reason` key at
   all.
5. `duration_ms` of 54, 10, 10 — a query executed and returned.

**The scope limit.** The zero is honest but it is not yet a measurement of a
populated queue, for two compounding structural reasons:

- `expires_at` was created *by this very deploy*. Every pre-existing reservation
  row therefore holds `NULL` in it, and the candidate SQL explicitly excludes
  `r.expires_at IS NULL`. Legacy rows are invisible to the sweep by construction
  and will remain so.
- Marketplace card payments are hard-paused, so no new reservations with a
  populated `expires_at` are being created.

Consequently `candidates=0` is currently *guaranteed*, not *discovered*. The
sweeper is provably healthy and provably querying; what it cannot yet do is
demonstrate correct behaviour on a non-empty candidate set in production. That
demonstration exists only in the test suite (`test_05_bootstrapped_schema_finds_a_real_candidate`
and the 20-case matrix), which is real evidence but is not production evidence.
No inventory leak is being masked by this — a leak would require rows the sweep
should have seen and did not, and no such rows can exist while every row's
`expires_at` is NULL.

---

## Zero-tolerance conditions

Re-evaluated only against the four successful scans, as required.

| Field | Result |
|---|---|
| **PAID RELEASE CANDIDATES (0 REQUIRED)** | **0. PASS** — asserted from four scans that each reached `status=ok, failed=0`, not from a failed or skipped scan. Scope-limited exactly as described above: the candidate population was empty, so no paid candidate could appear. |
| **REFUNDED RELEASE CANDIDATES (0 REQUIRED)** | **0. PASS** — same basis, same scope limit. |

---

## Degradation telemetry and count semantics

Both verified in controlled test evidence. **Production schema was not broken to
test this**, per the directive.

Partial-schema fallback at the worker boundary:

```
REQ-11  sweep outcome  status = degraded | reason = schema_missing | failed = 1
                       candidates = 0 | released = 0
REQ-11  HEARTBEAT      last_sweep_status = degraded | last_sweep_reason = schema_missing
                       last_sweep_failed = 1 | last_sweep_dry_run = True
REQ-11  rows mutated by the blocked sweep = 0
        RESERVATION_SWEEP_SCHEMA_BLOCKED reason=schema_missing missing=expires_at
                                          error=None dry_run=True
```

`last_sweep_reason` reaching the heartbeat is the operational payoff: "this
database needs a migration" and "a row misbehaved" used to arrive identically as
`degraded, failed=1`. They are now distinguishable without reading logs.

Count semantics, unchanged by this commit and re-proven:

```
REQ-15  commit OK + close FAILS -> status='degraded'  released=3 candidates=4
                                   heartbeat status='degraded' released=3
REQ-15  commit FAILS            -> status='error'     released=None candidates=None
                                   heartbeat status='error' released=0
```

---

## Safety posture

| Field | Result |
|---|---|
| **PAYMENT PAUSE (TRUE REQUIRED)** | **TRUE.** `marketplace_card_payments_paused()` returns a hardcoded `True` with no environment dependency — it cannot be flipped by a variable edit, only by a code change. Unconditionally paused in production. |
| **DRY RUN (TRUE REQUIRED)** | **TRUE**, confirmed from the worker's own runtime log rather than from the variable panel: `RESERVATION_SWEEP_CONFIG enabled=True dry_run=True interval=300 batch=50 stripe_key_present=True`, on both the initial boot and the post-restart boot. `MARKETPLACE_RESERVATION_SWEEPER_DRY_RUN` and `MARKETPLACE_RESERVATION_SWEEPER_ENABLED` are both present on the service. **Not changed. Per Stage 17, `DRY_RUN=false` was not set and no production reservation mutation was authorized or performed.** |
| **MUTATION EVIDENCE** | Zero rows mutated. Every cycle ran `dry_run=True` with `released=0, captured=0, provider_calls=0`. No Stripe call was made. |
| **`payment_intent.canceled`** | **OWNER ACTION REQUIRED.** No Stripe connector is available in this session, so the live dashboard webhook configuration cannot be read. Per the directive this stays OWNER ACTION REQUIRED rather than being marked verified on the strength of the code path alone. To close it: confirm in the Stripe dashboard that the production webhook endpoint subscribes to `payment_intent.canceled`, and that a test event reaches the handler. |

---

## Summary table

| Field | Value |
|---|---|
| MAIN SHA | `3b431221a8251ff4542fe4e8488290da4453e61d` |
| RAILWAY SHA | `3b431221a8251ff4542fe4e8488290da4453e61d` |
| DEPLOY | SUCCESS |
| SCHEMA BOOTSTRAP | SUCCESS — 7 columns added by the worker |
| SCHEMA READY SIGNAL | PRESENT (`RESERVATION_SCHEMA_READY columns=15`) |
| WORKER INDEPENDENT OF WEB TRAFFIC | YES |
| CYCLE 1 | 02:25:17.838Z — ok / None / 0 / 0 / 0 |
| CYCLE 2 | 02:30:18.522Z — ok / None / 0 / 0 / 0 |
| CYCLE 3 | 02:35:19.210Z — ok / None / 0 / 0 / 0 |
| FAILED (0 REQUIRED) | 0 — PASS |
| SCANNED | 0 |
| CANDIDATES | 0 (non-vacuous query, structurally-empty population — see above) |
| WOULD RELEASE (tx / units) | 0 / 0 |
| WOULD DEFER | 0 |
| RECONCILED | 0 |
| PAID RELEASE CANDIDATES (0 REQUIRED) | 0 — PASS |
| REFUNDED RELEASE CANDIDATES (0 REQUIRED) | 0 — PASS |
| CADENCE | ~300.69 s, no hot loop — PASS |
| RESTART DURABILITY | PASS (`added=-` on re-boot; idempotent) |
| PAYMENT PAUSE (TRUE REQUIRED) | TRUE |
| DRY RUN (TRUE REQUIRED) | TRUE |
| `payment_intent.canceled` | OWNER ACTION REQUIRED |
| **FINAL VERDICT** | **DRY-RUN PASS** |

---

## What is not yet proven, and what would prove it

Nothing here authorizes leaving dry-run. Two things remain outstanding, and both
need a human:

1. **A production scan over a non-empty candidate set.** This cannot happen until
   reservations are created with a populated `expires_at`, which cannot happen
   while card payments are paused. The honest sequencing is: unpause is a
   prerequisite for the observation, not the other way round.
2. **`payment_intent.canceled` webhook verification** in the live Stripe
   dashboard.

`MARKETPLACE_RESERVATION_SWEEPER_DRY_RUN` remains `true` and must stay `true`.
