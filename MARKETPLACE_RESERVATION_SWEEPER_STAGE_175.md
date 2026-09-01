# Stage #175 — The Durable Reservation Sweep Service

Service only. No worker wiring, per Stage 20.

Built 2026-08-31 on branch `codex/emergency-live-audio-recovery`.

---

## What was built

`services/marketplace_reservation_sweeper.py` (466 lines, new). One entry point,
`run_reservation_expiry_sweep(cur, *, now=None, limit=None, dry_run=False,
fetch_status=None, recheck_seconds=None)`, which runs a single bounded batch and
returns a structured summary. It contains no loop, no sleep, no thread and no
opinion about wall-clock time; the worker in #176 supplies its own cadence.

Two supporting changes in `services/marketplace_cart_routes.py`:

- `reconcile_deferrals INTEGER` appended to `_RESERVATION_LIFECYCLE_COLUMNS`.
- `note_reservation_deferral(cur, seller_transaction_id, *, now=None)`, placed
  beside `capture_inventory_reservation` and `release_inventory_reservation` so
  every write to a reservation row still lives in one module.

`tests/marketplace/test_reservation_sweeper.py` (44 tests, new).

### Why the deferral column had to exist

`decide_from_status` already took a `deferrals: int` and already had a
`MAX_DEFERRALS` bound, but nothing durable stored the count. Every sweep would
have started again at zero, so `requires_action` would have deferred forever and
the `buyer_never_completed` release would have been unreachable in production —
a bound that exists in the decision table and never fires. The column is added
by the same defensive idempotent `ALTER TABLE` mechanism as the other lifecycle
columns, because this repo has no migration framework and editing the `CREATE
TABLE` would only reach fresh databases.

Test 30 is the one that proves this end to end: six sweeps at six injected
timestamps, asserting the counter increments durably each time, and a seventh
that releases.

---

## Deliberate deviations from the directive

**Release reason.** Stage 7 suggests the literal string `reservation_expired`.
The sweeper passes `reservation_policy.REASON_EXPIRED` (value `"expired"`)
instead. `RELEASE_REASONS` is a closed frozenset and
`release_inventory_reservation` normalises any unrecognised reason to
`REASON_MANUAL` — so passing the directive's literal would have produced exactly
the `manual` default that Stage 7 explicitly forbids. Test 18 asserts every
release reason is a member of `RELEASE_REASONS` and is not `manual`.

**Capture is half-performed on purpose.** When Stripe reports `succeeded` on a
still-held reservation, a `payment_intent.succeeded` webhook was lost. The
sweeper consumes the hold — the protective half, which makes the stock
permanently unreturnable so no later sweep can hand a paid item back — and
stops. Marking the order paid and creating it involves Connect routing and
buyer-visible order creation, and a second copy of that inside a sweeper is
precisely the duplication Stages 5-6 removed. The row is counted under
`needs_attention` and logged with `needs_order_repair=1` so an operator sees a
settled payment whose order never materialised rather than the sweeper silently
inventing one.

**Test count.** The directive asks for 24 cases. 44 are present; the extras are
the parametrised expansions over `AWAITING_BUYER_STATUSES` and the three
Postgres/config-portability cases.

---

## Stage-by-stage results

| Field | Result | Evidence |
| --- | --- | --- |
| Entry point shape | PASS | `run_reservation_expiry_sweep(cur, *, now, limit, dry_run, fetch_status, recheck_seconds)` |
| Bounded candidate selection | PASS | test 22 — `limit=5` over 20 expired rows releases exactly 5 |
| Index-backed query shape | PASS | `WHERE` leads `r.status = ?` then `r.expires_at <= ?`; `idx_mkt_reservations_status_expires` created in `_ensure_reservation_lifecycle_columns` |
| Deterministic ordering | PASS | test 23 — `ORDER BY expires_at ASC, seller_transaction_id ASC` |
| No payment intent → release | PASS | test 09, 0 provider calls |
| Local settled → protected | PASS | test 07, excluded before any provider call, both `paid` and `refunded` |
| Stripe `canceled` → release | PASS | test 10, terminal status `canceled`, reason `payment_canceled` |
| Stripe `succeeded` → never released | PASS | test 11, stock unchanged, reservation `captured` |
| Stripe `processing` → defer | PASS | test 12, 15 — never force-released even past the bound |
| Awaiting-buyer → bounded defer | PASS | test 13, 14 across all four statuses |
| Unknown status → defer | PASS | test 16, `needs_attention` set |
| **Stripe unreachable → defer, never release** | **PASS** | test 17 — 10 expired rows, provider raises on all, `released == 0`, stock unchanged |
| Release reason is a real machine reason | PASS | test 18 — never `manual` |
| Shared settlement path only | PASS | test 37 — AST-level: no `UPDATE`/`INSERT`/`DELETE` literals, `release_inventory_reservation` not called, `settle_failed_transactions` called |
| Per-row failure isolation | PASS | test 27 — 3 candidates, middle row raises, 2 released, 1 failed, failed row still `held` |
| Idempotency (re-run safe) | PASS | test 28 — second sweep is a no-op, stock credited exactly once |
| Race safety (capture wins) | PASS | test 29 — capture between selection and settlement, sweep releases nothing |
| Dry run: no release | PASS | test 19 — `would_release=1`, `released=0`, stock and tx status unchanged |
| Dry run: no backoff state | PASS | test 20 — `reconciled_at` still NULL, deferral count still 0 |
| Dry run: no capture | PASS | test 21 |
| Structured result contract | PASS | test 02 — 17 keys, no log parsing required |
| Telemetry event names | PASS | test 35 — all seven emitted in one sweep |
| No secrets in telemetry | PASS | test 36 |
| Backoff without a second scheduler | PASS | test 24, 25 — reuses the existing `reconciled_at` timestamp |
| Provider traffic bounded | PASS | test 26 — 5 rows, 3 candidates, exactly 2 Stripe reads, and the right two |
| Injected time only | PASS | test 31 — same database, two `now` values, two answers |
| Config clamps | PASS | test 32, 33 |
| SQLite + Postgres portability | PASS | 6 `?` placeholders, 6 params, 6 `%s` after `db._replace_question_placeholders`, zero stray percent literals; degraded-schema path test 34 |
| Wiring guard — new private copies | **0** | `test_reservation_webhook_wiring.py` walks every module under `services/`; 50 passed |
| `marketplace_card_payments_paused()` | **TRUE** | asserted live and by `test_marketplace_cash_payment_pause.py` |

### Gate runs

```
tests/marketplace/test_reservation_sweeper.py          44 passed
tests/marketplace/test_reservation_settlement.py  ┐
tests/marketplace/test_reservation_webhook_wiring.py ┘ 50 passed
combined payment gate set                             157 passed in 1.57s
tests/marketplace/ (full directory)               150 passed, 1 failed
```

The one failure is `test_post_settlement_finance.py::test_stripe_charge_cumulative_refund_applies_only_delta`,
and three files fail to collect at all. All four are the same pre-existing
environment divergence: `services/feature_flag_engine.py:10` does `from datetime
import UTC`, which requires Python 3.11. The sandbox runs 3.10.12; production
pins 3.11 via nixpacks. The traceback never enters any module this stage
touched, and none of those tests reference the sweeper or the cart routes.

---

## Stage 20 gate

| Precondition | State |
| --- | --- |
| Service tests | PASS (44/44) |
| Wiring guard | PASS (0 new private release/close copies) |
| Payment pause assertion | PASS (remains TRUE) |
| Idempotency / race tests | PASS (tests 28, 29) |

All four preconditions are met, so #176 is unblocked. Nothing has been wired
into `coinpilotx-pulse-worker` in this stage.

---

## Carried into #176

The sweep costs at most one Stripe read per candidate, so cadence and batch size
are the same dial. `DEFAULT_BATCH_LIMIT = 50` and
`DEFAULT_MIN_RECHECK_SECONDS = 300` are chosen against a sweep interval well
above the pulse worker's 20-second base loop; the worker should run the sweep on
its own sub-cadence rather than every cycle. Both are environment-tunable
(`MARKETPLACE_RESERVATION_SWEEP_BATCH`, `MARKETPLACE_RESERVATION_SWEEP_RECHECK_SECONDS`)
and both are clamped, so a retune needs no redeploy and a typo cannot turn one
sweep into a full scan.

First production run should be `dry_run=True`. The dry-run path evaluates every
decision, makes the same provider calls, and writes nothing — so the
`would_release` count is a real measurement of how much stock is currently
stranded, taken before anything is released.

---

## FINAL VERDICT: STAGE #175 PASS
