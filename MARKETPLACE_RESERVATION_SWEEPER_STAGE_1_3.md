# Reservation Expiry Sweeper — Stages 1–3

Worker selection, durable query surface, and TTL authority. No code was changed
by this stage; it is the evidence the build in Stage 4 rests on.

Evidence captured 2026-08-31 against Railway project `coinpilotx-alert-worker`
(`111b3838-09d4-4f13-8b8b-6ed332bad06f`), production environment
`8bf01340-99d0-49be-a951-abffc17aa4d3`.

---

## Stage 1 — Which worker owns the sweep

### The preference the directive states, and why it cannot be honoured literally

The directive says to prefer the worker already responsible for
marketplace/payment maintenance. No such worker exists. `expire_offers` and
`run_expiry_sweep` — the only marketplace maintenance routines already in the
tree — have no worker caller at all; their only callers are the test suite and
an admin POST route. Marketplace maintenance today runs when a human presses a
button, which is the same class of defect this mission exists to fix, one layer
up. So the choice is between workers that are adjacent to the domain rather than
one that owns it.

### The Procfile is not the production topology

`Procfile` declares `web`, `undx_worker`, `email_worker`, `ads_worker`,
`alert_worker`, `media_worker`. Railway overrides it per service with an
explicit start command, so the deployed set differs in both directions:
`pulse_worker.py` and `telegram_worker.py` are absent from the Procfile but
*are* deployed as their own services, and there is no standalone email worker
service. Selecting a worker from the Procfile alone would have produced a
plausible, wrong answer. Every candidate below was confirmed against
`get-service-config` and live deploy logs.

### Candidates

**`coinpilotx-pulse-worker`** (`094b83d0-6c69-4e0c-9230-a04b9f0d55f4`) —
`python pulse_worker.py`, RAILPACK, 1 replica in `sfo`. A `while True` loop with
a floor-clamped `time.sleep(max(5, PULSE_WORKER_SLEEP_SECONDS))`, default 20s.
Every cycle is wrapped in `try/except Exception` that logs and writes a
`record_worker_heartbeat(..., "error", ...)` rather than exiting, so a bad cycle
cannot kill the process; Railway restarts it if the process dies anyway. Current
responsibilities are feed job processing (`pulse_feed_engine.process_pending_jobs`)
and the Space AI post rotation. Carries **both** `DATABASE_URL` and
`STRIPE_SECRET_KEY`. Live: boot at 02:54 on SHA `6a051182ba65` logging
`PULSE_WORKER_START database_url=True`, and rotation cycles landing on schedule
through 22:59.

**`pulsesoc-ads-worker`** (`bf5a0a9a-6b59-42c3-be16-91692a4cb3a8`) —
`python pulse_ads_worker.py`, 1 replica in `sfo`, ~300s cadence confirmed from
the log interval. Architecturally the closest analogue to what the sweeper needs:
a structured multi-section cycle that already emits a machine-readable
`ADS_WORKER_CYCLE` summary with a `billing` section, and already does periodic
payment-adjacent maintenance (billing drift checks) on a slower sub-cadence.
It has `DATABASE_URL`. It does **not** have `STRIPE_SECRET_KEY`, and grepping
`pulse_ads_worker.py` for `stripe` returns nothing — its billing work is
DB-level drift detection that never calls the provider.

**`coinpilotx-undx-worker`** (`c2c9b804-3e7e-4a5d-9061-54542a5f7d89`) —
`python undx_worker.py`. `DATABASE_URL` but no `STRIPE_SECRET_KEY`. Domain is the
AI mission/execution layer; putting inventory settlement inside the agent runtime
would couple money movement to the kill switches that govern agent writes.
Rejected on domain grounds regardless of configuration.

**`PulseSoc Command Center Worker`** (`0a35d448-e65e-4e45-bafb-812dfa57c76f`) —
a gunicorn HTTP service, not a loop. Wrong shape: a sweep hosted here would need
an external caller to drive it, which is the request-thread timer the directive
forbids. `DATABASE_URL`, no Stripe key.

**`CoinPilotX`** (`ce41f7c5-b882-4aa7-81b3-06de73fded31`) — the web service. Holds
the full Stripe key set, and is exactly where the sweep must not live.

`python alert_worker.py` is crypto-market alerting and unrelated;
`coinpilotx-media-engine` is transcode; the three `tmp-`/`TEMP-` services are
disposable.

### Why Stripe reachability decides this

Stage 5 reconciliation is not optional garnish on the sweep — it is the guard
that stops the sweep from releasing stock out from under a payment that is still
live. `decide_for_reservation` consults Stripe for any row carrying a
`stripe_payment_intent_id`, and on any exception from `fetch_status` it returns
`DECISION_DEFER` with `needs_attention=True`, never a release. That failure mode
is deliberate and correct: during a Stripe outage every expired reservation
reconciles at once, and if that produced releases, one provider incident would
empty every hold in the system and resell paid orders wholesale.

The consequence for worker selection is precise. A worker without
`STRIPE_SECRET_KEY` would be **safe but inert** — it would defer every row that
ever reached Stripe, forever, while logging that it needs attention. It would
look like it was working. The original bug was a hold that nothing ever
collected; a Stripe-blind sweeper reproduces that outcome with more logging.

Among live loop-shaped workers, `coinpilotx-pulse-worker` is the only one that
already has the key. Its Stripe module genuinely initialises in-process — the
boot log carries `Railway Stripe warning: STRIPE_PRICE_ID is missing`, which is
the config check running inside that process, not the web dyno's.

### Decision

**`coinpilotx-pulse-worker`**, hosting a new sweep section inside the existing
`while True` cycle.

The honest cost of this choice is that the name will not describe the whole job:
a marketplace inventory sweep inside a service called "pulse worker" is a
discoverability tax on whoever debugs it next. That is paid down with an explicit
`RESERVATION_SWEEP_CYCLE` log line and a heartbeat metadata section, so the work
is findable by what it logs rather than by which service it sits in.

The alternative — adding `STRIPE_SECRET_KEY` to `pulsesoc-ads-worker` and using
its better-fitting structured cycle — is a production credential change on a
service that does not currently hold a live secret key. That is an owner
decision, not a mission decision, and it is not required: the pulse worker path
needs no configuration change at all. Recorded here as the fallback if the sweep
later outgrows a 20-second cadence and wants the ads worker's 5-minute one.

Cadence: the sweep will run on its own interval inside the pulse loop rather than
every 20 seconds. A 15-minute TTL does not need four sweeps a minute, and the
reconciler makes a Stripe API call per candidate row.

---

## Stage 2 — The durable query surface already exists

Confirmed present in `services/marketplace_cart_routes.py`, added by the Stage 1–2
schema work:

`_RESERVATION_LIFECYCLE_COLUMNS` adds `reserved_at`, `expires_at`, `released_at`,
`captured_at`, `release_reason` and `reconciled_at` to
`marketplace_inventory_reservations`. They are added by defensive `ALTER TABLE`
in `_ensure_reservation_lifecycle_columns` rather than by editing the `CREATE
TABLE`, because this repo has no migration framework and editing the create
statement would only reach fresh databases while silently skipping production.
Column introspection goes through `services.db.get_table_columns` rather than
`PRAGMA`, since production is PostgreSQL where `PRAGMA` raises and poisons the
surrounding transaction — the failure that historically made these ALTERs no-ops.

The index the sweeper needs is already there:

```sql
CREATE INDEX IF NOT EXISTS idx_mkt_reservations_status_expires
  ON marketplace_inventory_reservations (status, expires_at)
```

It was created for this sweeper specifically. `(status, expires_at)` is the only
query shape the sweep has; without it the sweep degrades to a full scan of every
reservation ever taken.

`reconciled_at` is the column that lets the sweep back off a deferred row without
losing it — the durable half of the defer decision.

**No schema work is required in Stage 4.**

---

## Stage 3 — TTL authority is canonical and already injectable

`services/marketplace_reservation_policy.py` is the single source of truth, and
it is already shaped for a long-lived worker:

- `DEFAULT_TTL_SECONDS = 900` (15 minutes), clamped into `[300, 3600]`.
  `reservation_ttl_seconds()` re-reads `MARKETPLACE_RESERVATION_TTL_SECONDS` on
  **every call** rather than caching at import, precisely so retuning the window
  does not require redeploying the worker. An unparseable value falls back to the
  default instead of raising — a typo in configuration must not take the store
  offline.
- `EXPIRY_GRACE_SECONDS = 60` absorbs clock skew between the web dyno and the
  worker, and gives a `payment_intent.succeeded` that is racing the deadline a
  moment to land before the sweep wakes the reconciler.
- `is_expired(expires_at, *, now=None, grace_seconds=None)` takes injected time,
  so the Stage 17–20 test matrix can drive the full table deterministically
  without sleeping.
- A row with an unparseable or absent `expires_at` returns `False` from
  `is_expired`. Pre-migration rows are in exactly that position, and inventing a
  retroactive deadline for them would release stock for orders that may be
  legitimately mid-flight. `legacy_backfill_expiry(created_at)` gives them a real
  deadline anchored to their own `created_at`, so an already-stale legacy hold
  becomes immediately collectable rather than winning a fresh full TTL.

`REASON_EXPIRED` is already a member of `RELEASE_REASONS`, so the sweep has its
audit reason without extending the vocabulary.

**No policy work is required in Stage 4.**

---

## Constraint carried into Stage 4

The sweeper calls `settle_failed_transactions` and nothing else. No direct
`UPDATE marketplace_listings`, no direct `UPDATE seller_transactions`, no direct
`release_inventory_reservation`. Stages 5–6 collapsed six private copies of
"release the hold, then move the transaction to a terminal status" into that one
function, and
`tests/marketplace/test_reservation_webhook_wiring.py::test_failure_branches_do_not_pair_a_release_with_their_own_status_update`
now walks every module under `services/` — so a private copy inside the sweeper
would fail CI rather than merely being poor style. That guard is the reason the
constraint is enforceable rather than aspirational.
