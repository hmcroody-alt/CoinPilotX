# Payments Reconciliation — Runbook

The sweep that checks whether the money chain actually did what its metrics
claim. Detect-and-report only: it opens incidents, and it never repairs,
re-queues or transitions anything. Switching it on cannot move a cent in either
direction.

Authority: `services/business_os/payments/reconciliation.py` (the engine) and
`services/payments_reconciliation_cycle.py` (the caller). Hosted by
`pulse_worker`.

## 1. What changed

The engine was mature and reachable only from two admin routes. **Nothing
unattended called it.** A reconciler that runs when a human remembers to open an
admin page is a reconciler that finds yesterday's problem next week.

It now has a governed caller, and one new check — `marketplace_settlements` —
that can see a settlement stranded mid-chain. That condition is invisible to
every other check, because to the ledger the money is exactly where it belongs.

## 2. Turning it on

| Variable | Range | Default |
|---|---|---|
| `PAYMENTS_RECONCILIATION_ENABLED` | truthy / falsey | **off** when unset, blank or unparseable |
| `PAYMENTS_RECONCILIATION_SECONDS` | 300–86400 | 3600 |

An unparseable interval falls back to the **default**, not to the floor. A typo
should not silently produce the most expensive schedule available.

Every condition it detects is measured in hours or days, and the sweep is the
most expensive read in the worker, which is why the floor is five minutes rather
than one.

This flag is safe to enable at any time, including before card payments are
switched on. Doing so early is the recommendation: it establishes what "clean"
looks like on a quiet system, so the first finding after activation means
something.

## 3. The checks

`run_all()` runs eight, in this order, each contained so a failure in one cannot
blind the others:

| Check | Finds |
|---|---|
| `ledger_balances` | Double-entry drift |
| `ad_wallets` | Wallet balance vs. ledger |
| `funding_sessions` | Sessions that never resolved |
| `webhook_inbox` | Dead-lettered provider events |
| `suspense` | Funds parked with no owner |
| `seller_payouts` | Stale payout records |
| `rewards` | Rewards drift |
| **`marketplace_settlements`** | **Settlements stopped mid-chain** (§4) |

The list is pinned by an **exact** assertion in
`tests/business_os_finance/test_reconciliation.py` — not a superset. A check
silently dropped from `run_all` is indistinguishable from one that found
nothing, and the engine is only worth its cost if the list of what it looks at
stays deliberate.

## 4. The marketplace settlement check

Five findings. Two are critical regardless of amount.

| Code | Severity | Threshold | Meaning |
|---|---|---|---|
| `payout_scheduled_without_provider_id` | **critical** | 6h | **Unreachable.** See below |
| `protection_hold_without_end` | **critical** | immediate | A hold with no `protection_ends_at` can never elapse |
| `protection_hold_not_released` | warning | 24h past the hold end | The eligibility sweep is not running |
| `eligible_but_never_scheduled` | warning | 24h | The payout scheduler is not running |
| `payout_state_not_in_state_machine` | **critical** | immediate | A state outside `PAYOUT_STATES` |
| `commercial_snapshot_mismatch` | varies by amount | — | `net != gross − reversed` |

### The critical one

`run_once` transitions a settlement to `scheduled` **before** the two Stripe
calls, and writes `provider_payout_id` only after both succeed.
`apply_provider_event` matches incoming webhooks **by that column**. So a crash
between the two leaves a row no Stripe webhook can ever reach — nothing will
move it again, and the seller's money stays fenced on the platform side
indefinitely.

Critical regardless of amount, because the severity is about permanence, not
size. Recovery procedure: `PAYOUT_SCHEDULER_RUNBOOK.md` §7.

### Why the hold grace is measured from the hold end

`protection_hold_not_released` measures from `protection_ends_at`, **not** from
`updated_at`. Measuring from the last write would let any unrelated UPDATE on
the row restart the clock, and a row being touched is not the same as a row
making progress.

### The arithmetic check

`snapshot_drift(row)` — the only check with no selective predicate, so it scans
up to `SNAPSHOT_SCAN_LIMIT` (5000) rows and reports `scan_truncated: true` when
it stops early. Truncation is a **named field**, not a silent omission: an
unexamined row must not look like a balanced one.

The implementation is shared with `marketplace_commercial_operations.reconcile()`
rather than duplicated. A second copy of the expression would be free to drift
from the first, which is a poor failure mode for the function whose whole job is
detecting drift. `tests/marketplace/test_reconciliation_cycle.py::test_both_reconcilers_ask_the_same_question`
holds that.

A row that cannot be read at all — a column a migration has not added yet — is
caught and **reported**, not allowed to abort the scan. One bad row must not
blind the sweep to the other 4999.

## 5. Reading the output

### Incidents

Findings land in `financial_incidents` with an idempotent key:

```
{incident_type}:marketplace_settlement:{tx_id}:{code}
```

`incident_key` is UNIQUE, so a repeat sweep **refreshes** the incident rather
than duplicating it. A condition that persists for a week is one incident that is
a week old, not 168 incidents.

Listing is capped at `MARKETPLACE_INCIDENT_CAP` (50), but the **count is never
capped**. A systemic stall that hits 4000 settlements reports 4000 in
`occurrences_this_run` and lists 50. Capping the count would turn the worst case
into the one that looks smallest.

### The heartbeat

`heartbeat_metadata(state)` returns `{"reconciliation_enabled": false}` when off,
and otherwise the interval plus the last cycle's metrics:

| Field | Meaning |
|---|---|
| `last_reconciliation_at` | When |
| `last_reconciliation_incidents` | Opened or refreshed this run |
| `last_reconciliation_check_errors` | Checks that raised |
| **`last_reconciliation_critical_open`** | **Standing state — see below** |
| `last_marketplace_stuck_scheduled` | The critical count |
| `last_marketplace_hold_not_released` | |
| `last_marketplace_never_scheduled` | |
| `last_marketplace_snapshot_mismatches` | |
| `last_marketplace_scan_truncated` | |
| `last_reconciliation_error` | The cycle itself failed |

### `open_critical_incidents` is not this run's output

It counts `open` **and** `acknowledged`. Acknowledged means someone has seen it,
nobody has fixed it, and the money is still wherever it was. Only `resolved` and
`ignored` stop counting, and both require a written note.

This exists because a sweep that opens nothing while yesterday's critical is
still open is **not a clean sweep**, and an alerting caller reading only
`last_reconciliation_incidents` would call it one. It is counted after the sweep,
so it includes anything just opened.

An incident cannot be silenced by being read.

### Alerting

A nonzero critical count logs at ERROR:

```
PAYMENTS_RECONCILIATION_CRITICAL open=%s incidents_touched=%s
```

Otherwise the cycle logs at INFO. Alert on the ERROR line; it is the intended
hook and it fires on standing state, not only on new findings.

## 6. Responding

| Finding | First action |
|---|---|
| `payout_scheduled_without_provider_id` | **Establish which Stripe legs completed before touching anything.** `PAYOUT_SCHEDULER_RUNBOOK.md` §7 |
| `protection_hold_without_end` | Find why the row reached the hold without a delivery. Set an end or move it deliberately; do not assume elapsed |
| `protection_hold_not_released` | Check `MARKETPLACE_SETTLEMENT_SWEEP_ENABLED` and whether `pulse_worker` is running |
| `eligible_but_never_scheduled` | Check the payout worker's `_mutation_preconditions` reason in the heartbeat |
| `payout_state_not_in_state_machine` | A write bypassed `transition_payout`. Find the writer before fixing the row |
| `commercial_snapshot_mismatch` | Compare against the Stripe charge and any refunds. Small drift is usually rounding; a whole order's worth is not |

Resolve with a written note saying what was done. The note is the only durable
record of which of the three Stripe cases a stuck payout turned out to be.

## 7. Worker wiring

The cycle runs **last** in `pulse_worker`'s tick, after the reservation sweeper,
the release cycle and the payout worker — so a finding is about the state the
chain settled into on this tick, not the one it started from. It carries its own,
much longer deadline.

`tests/marketplace/test_reservation_sweep_worker_wiring.py` holds the seam:
every marketplace/reconciliation module reached by the worker must be one of the
declared `HOSTED_MARKETPLACE_SEAMS`. A direct
`services.business_os.payments.reconciliation` import from the worker fails that
test, which is the drift the seam exists to prevent.

## 8. Tests

`tests/marketplace/test_reconciliation_cycle.py` — 48 tests, including that the
sweep transitions nothing, that running twice does not open the same finding
twice, that a systemic stall is counted in full though not listed in full, that
acknowledged is not resolved, and that a broken marketplace check does not blind
the other seven.

Run it in its own pytest process.
