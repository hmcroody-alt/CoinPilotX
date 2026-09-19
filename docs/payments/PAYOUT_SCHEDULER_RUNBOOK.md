# Payout Scheduler — Runbook

The job that moves a seller's cleared earnings off the platform balance. Two
irreversible Stripe calls per settlement. This document is how to turn it on,
how to watch it, and what to do when it stops.

Authority: `services/marketplace_payout_worker.py` (the governed caller) and
`services/marketplace_payout_scheduler.py` (the work itself). Hosted by
`pulse_worker`.

## 1. What it does

For each settlement in `eligible`:

```
transition_payout(tx, "scheduled")          # DB
  ↓
provider_transfer(...)                       # Stripe: platform balance → connected account
  ↓
provider_create(...)                         # Stripe: connected account → seller's bank
  ↓
mark_payout_submitted(...)  +  UPDATE provider_payout_id
```

This is **separate charges and transfers**. The buyer paid the platform; the
transfer and the payout are two distinct legs and they happen in that order.

Both legs are irreversible from this platform's side. Money can be *requested*
back from a seller; it cannot be taken back. Every gate below exists because of
that sentence.

## 2. Turning it on

Three independent switches, all off when unset, blank or unparseable.

| Stage | Variables | Effect |
|---|---|---|
| **1 — observe** | `MARKETPLACE_PAYOUT_WORKER_ENABLED=true` | Cycle runs **read-only** and reports what it *would* pay |
| **2 — authorise** | `…_DRY_RUN=false` **and** `…_OWNER_AUTHORIZED=true` | Money moves |

Do stage 1 first. Its report is the evidence for deciding to do stage 2. An
operator who sets all three at once has skipped the only cheap opportunity to
notice that the batch contains something wrong.

Tuning:

| Variable | Range | Default |
|---|---|---|
| `MARKETPLACE_PAYOUT_WORKER_SECONDS` | 60–3600 | 600 |
| `MARKETPLACE_PAYOUT_WORKER_BATCH` | 1–200 | 25 |

Both are clamped, so a typo cannot produce a hot loop or an unbounded batch.

Railway variables reach a container **only at boot**. Setting any of these
without a redeploy changes nothing in the running process; verify with
`railway ssh`, not with `railway variables`.

## 3. Why it may refuse — `_mutation_preconditions()`

It returns a *reason string*, not a bool, so the heartbeat can say which gate is
shut. "Disabled" and "authorised but running on SQLite" need different operator
responses and a bare `False` conflates them.

| Reason | Meaning | Action |
|---|---|---|
| `dry_run` | `DRY_RUN` is not explicitly `false` | Expected during stage 1 |
| `owner_not_authorized` | `OWNER_AUTHORIZED` is not explicitly `true` | Expected during stage 1 |
| `no_leader_lock_off_postgres` | `db.IS_POSTGRES` is false | **Misconfiguration.** The worker is pointed at SQLite |
| `stripe_not_configured` | No usable key | Reported once rather than discovered one row at a time — every row would fail identically |
| `stripe_mode_unrecognized` | Key prefix unreadable | **Stop.** The three switches say the owner authorised *a* payout run; they cannot say the owner knew which Stripe it would reach. An unreadable key is treated as live |

## 4. The leader lock

`pg_try_advisory_lock(620260917)` — try, never wait. A replica that blocked here
would pile up behind a cycle already doing the work it wanted to do.

The lock is **not** what prevents a double payout — the settlement state machine
is, since `eligible → scheduled` happens once. What the lock prevents is two
replicas racing into the same transition and both getting far enough to call
Stripe. It is session-scoped and released at the end of the cycle; a failure to
unlock is logged, never raised, because failing to unlock must not fail the
cycle.

**On SQLite the lock is unavailable**, which is why `no_leader_lock_off_postgres`
is a hard precondition rather than a warning.

## 5. Account resolution

`resolve_account()` reads the seller's Connect snapshot **fresh every cycle**,
deliberately not cached and deliberately not taken from the settlement row.

A settlement became `eligible` using the capability flags as they were at sale
time. By the time it is paid the seller may have deauthorized PulseSoc or had
payouts disabled by Stripe. `request_payout` refuses a snapshot without
`payouts_enabled`, so reading fresh is what turns a revoked account into a
*skipped row* instead of a *failed Stripe call*.

## 6. Watching it

The worker heartbeat carries `heartbeat_metadata(state)`. Read:

- the `_mutation_preconditions` reason — if it is non-empty, nothing is moving
  and this says why;
- the per-cycle counts of scheduled / skipped / failed;
- `last_reconciliation_critical_open` from the reconciliation sweep, which is
  standing state and not this run's output (see
  `PAYMENTS_RECONCILIATION_RUNBOOK.md`).

## 7. The failure mode to know about

**A settlement in `scheduled` with no `provider_payout_id` is unreachable.**

`run_once` transitions to `scheduled` *before* the two network calls and writes
`provider_payout_id` only after both succeed. `apply_provider_event` matches
incoming Stripe webhooks **by that column**:

```sql
SELECT seller_transaction_id FROM marketplace_commercial_settlements
 WHERE provider_payout_id=? AND payout_state='scheduled'
```

So a hard crash between the transition and the write — process kill, container
eviction, Stripe timeout on the second leg — leaves a row that no Stripe webhook
can ever match. Nothing will move it again. The seller's money sits on the
platform side indefinitely, and from the ledger's point of view it is exactly
where it belongs.

This is why the reconciliation sweep reports
`payout_scheduled_without_provider_id` as **critical regardless of amount**, on a
six-hour threshold. It is the single most important reason to run that sweep.

### Recovering one

1. Find the row: `payout_state='scheduled'`, `provider_payout_id` null/empty,
   `updated_at` older than six hours.
2. In the Stripe dashboard, search transfers and payouts by the settlement's
   `transfer_group`. Determine **which legs actually happened**. There are three
   cases and they are not interchangeable:
   - neither leg → safe to return the row to `eligible` and let the scheduler
     retry;
   - transfer only → the money is on the connected account. Do **not** re-run the
     transfer. Issue the payout leg by hand and backfill `provider_payout_id`;
   - both legs → backfill `provider_payout_id` from the Stripe payout id. The
     webhook will then match on the next event.
3. Resolve the incident with a written note saying which case it was.

Never resolve one of these by re-running the cycle without first establishing
which legs completed. Re-running case two pays the seller twice.

## 8. Stopping it

Set `MARKETPLACE_PAYOUT_WORKER_DRY_RUN=true` and redeploy. That is the fastest
gate — it needs no coordination with the other two switches, and it leaves the
read-only reporting running so you can still see the backlog you have paused.

Setting `MARKETPLACE_PAYOUT_WORKER_ENABLED=false` also stops it, but blinds you
at the same time.

Neither unwinds an in-flight cycle. A cycle already inside `run_once` will
finish its current settlement; the lock is what stops the *next* replica, not
the current call.

## 9. Tests

`tests/marketplace/test_payout_worker_authority.py` — the three switches, the
precondition reasons, the clamps, the Postgres requirement, and that
`ENABLED` alone cannot move money.
