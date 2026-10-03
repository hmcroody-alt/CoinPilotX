"""The read side of the supplier checkout gate — §2–§5 of HARDEN THE MONEY PATH.

What this file is defending
---------------------------
``services/marketplace_drain_observability.py`` reports what
``marketplace_supplier_checkout`` is deciding across the whole catalogue. It
makes no decision and it writes nothing, which means the ways it can go wrong are
not the ways a gate goes wrong — they are reporting failures, and a reporting
failure is silent by construction. Three of them, each with a named test:

* **It disagrees with the gate.** The states here are *derived* from the verdict
  ``evaluate`` already returned. If any of them were re-derived from the
  underlying rows, the two would eventually diverge and the owner's screen would
  describe a system that does not exist. So every classification test below
  drives the real gate — real schema, real ``link_source``, real
  ``reconciliation_evidence`` — and asserts the name against a verdict the gate
  actually produced. No hand-built verdict dicts: a dict literal would still
  classify correctly after someone changed what the gate returns, which is
  precisely the failure this file exists to catch.
  ``test_every_branch_of_evaluate_has_a_name`` holds the structural half.

* **It reassures on an empty measurement.** A percentile of ``0`` reads as
  "every confirmation is perfectly fresh" and a rate of ``0.0`` reads as
  "measured, nothing wrong" — both are the most comforting possible rendering of
  "we measured nothing", and both are one-line changes away.
  ``test_an_empty_sample_is_none_not_zero`` and
  ``test_rates_are_none_when_there_is_no_denominator`` pin them.

* **It alerts on the wrong thing.** Production on 2026-10-03: 44 of 44
  purchasable drop-ship listings in ``UNVERIFIED_DRAIN_BEHIND``, and it had been
  that way for days. §1 forbids tuning the capacity that would change it, so
  that is a decision already made, not an incident — an alert on it is a siren
  that is always on. ``test_the_catalogue_wide_drain_behind_does_not_page_anyone``
  is the assertion that keeps the threshold derived from measurement rather than
  picked.

A pure unit suite: in-memory SQLite, schema from ``ensure_supplier_schema`` so
the tables are production's, and no provider call anywhere — the gate's own
design argument is that a checkout may not make a network call while holding a
write transaction, and a reader of that gate has even less business doing so.

The capacity projections (§5) are asserted against the *imported* ``CADENCE``
rather than against the numbers measured in production, so a cadence change moves
the test instead of silently invalidating it. Nothing here proposes a larger tick
budget; ``test_the_projection_does_not_assume_a_bigger_worker`` asserts that the
deployed numbers are the ones being modelled.
"""

import ast
import inspect
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from services import marketplace_drain_observability as obs
from services import marketplace_supplier_checkout as gate
from services import marketplace_supplier_schema as schema
from services import marketplace_variants as variants
from services.business_os.suppliers import revisions, worker

SELLER = 2001

DROPSHIP = 10      # supplier-fulfilled
DROPSHIP_B = 11    # a second one, so a state count can be more than 1
STOCKED = 20       # imported, merchant holds the stock
UNSOURCED = 30     # authored in PulseSoc, no supplier
UNBUYABLE = 40     # published but priced at zero — in the catalogue, not on sale

NOW = datetime(2026, 10, 3, 12, 0, 0)

#: Copied from ``fulfillment.ensure_schema`` and ``worker.ensure_schema`` rather
#: than created by calling them, because both open their own ``db.connect()`` and
#: this suite must stay inside its in-memory cursor. The copies are kept honest
#: by the structural tests at the bottom of the gate's own suite, which pin these
#: column names against their writers; duplicating that assertion here would be a
#: second authority on the same fact.
DRAIN_TICKS_DDL = ("CREATE TABLE business_os_supplier_drain_ticks ("
                   "scope TEXT PRIMARY KEY, started_at DOUBLE PRECISION, "
                   "completed_at DOUBLE PRECISION)")

SYNC_JOBS_DDL = ("CREATE TABLE IF NOT EXISTS business_os_supplier_sync_jobs ("
                 "id TEXT PRIMARY KEY, connection_id TEXT NOT NULL, "
                 "business_id TEXT NOT NULL, store_id TEXT NOT NULL, "
                 "kind TEXT NOT NULL, resource_id TEXT NOT NULL, "
                 "available_at DOUBLE PRECISION NOT NULL, "
                 "lease_until DOUBLE PRECISION NOT NULL DEFAULT 0, "
                 "lease_token TEXT, failures INTEGER NOT NULL DEFAULT 0, "
                 "last_verified_at DOUBLE PRECISION, evidence_hash TEXT, "
                 "last_error TEXT, UNIQUE(connection_id,kind,resource_id))")


@pytest.fixture(autouse=True)
def _reset_schema_cache():
    schema.reset_schema_cache()
    yield
    schema.reset_schema_cache()


@pytest.fixture()
def cur():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    # ``price_minor`` and ``quantity`` are here because ``snapshot``'s default
    # catalogue query reads them: production carries 196 published listings of
    # which only 44 are purchasable, and the denominator is the whole point.
    cursor.execute("""
        CREATE TABLE marketplace_listings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            seller_user_id INTEGER,
            title TEXT,
            price_label TEXT,
            price_minor INTEGER,
            status TEXT,
            approval_status TEXT,
            quantity INTEGER
        )
    """)
    rows = ((DROPSHIP, 'Dropshipped tee', 2000, 5),
            (DROPSHIP_B, 'Second dropshipped tee', 2000, 5),
            (STOCKED, 'Self-stocked tee', 2000, 5),
            (UNSOURCED, 'Hand-authored tee', 2000, 5),
            (UNBUYABLE, 'Published but unpriced', 0, 0))
    for listing_id, title, price_minor, quantity in rows:
        cursor.execute(
            "INSERT INTO marketplace_listings (id, seller_user_id, title, price_label, "
            "price_minor, status, approval_status, quantity) "
            "VALUES (?, ?, ?, '$20.00', ?, 'published', 'approved', ?)",
            (listing_id, SELLER, title, price_minor, quantity))
    cursor.execute(DRAIN_TICKS_DDL)
    cursor.execute(SYNC_JOBS_DDL)
    result = schema.ensure_supplier_schema(cursor, force=True)
    assert result["status"] == schema.STATUS_READY, result
    yield cursor
    conn.close()


# ---------------------------------------------------------------------------
# Fixture helpers — the same writers the gate's own suite uses
# ---------------------------------------------------------------------------

def bind(cur, *, listing_id=DROPSHIP, mode=None, sync_state=None,
         provider_variant_id=None):
    return variants.link_source(
        cur, listing_id=listing_id, seller_user_id=SELLER, provider="cj",
        provider_product_id=f"p{listing_id}",
        provider_variant_id=provider_variant_id or f"v{listing_id}",
        fulfillment_mode=mode or schema.MODE_DROPSHIP, sync_state=sync_state)


def add_variant(cur, *, listing_id=DROPSHIP, stock_state=None, value="S"):
    return variants.upsert_variant(
        cur, listing_id=listing_id, seller_user_id=SELLER,
        options=[{"name": "Size", "value": value}], sku=f"SKU-{listing_id}-{value}",
        price_cents=2000, cost_cents=800, currency="USD",
        stock_state=stock_state or schema.STOCK_IN_STOCK, stock_quantity=5)


def confirm(cur, *, listing_id=DROPSHIP, age_seconds=60, sync_state=None):
    """Backdate a confirmation through the format its only production writer uses.

    ``.replace(tzinfo=utc)`` before ``.timestamp()``, not after — the gate suite
    records what the other order costs: a bare naive ``.timestamp()`` reads as
    local time and puts every "backdated" stamp in the future, where a negative
    age sails through a ``>`` and makes allow assertions pass for the wrong
    reason.
    """
    moment = (NOW - timedelta(seconds=age_seconds)).replace(tzinfo=timezone.utc)
    cur.execute("UPDATE marketplace_product_sources SET last_synced_at=?, sync_state=? "
                "WHERE listing_id=?",
                (revisions._row_time(moment.timestamp()), sync_state or schema.SYNC_SYNCED,
                 int(listing_id)))


def _epoch():
    return NOW.replace(tzinfo=timezone.utc).timestamp()


def latch(cur, *, started=None, completed=None):
    cur.execute("INSERT INTO business_os_supplier_drain_ticks (scope, started_at, "
                "completed_at) VALUES('worker', ?, ?) ON CONFLICT(scope) DO UPDATE "
                "SET started_at=excluded.started_at, completed_at=excluded.completed_at",
                (started, completed))


def draining(cur):
    moment = _epoch()
    latch(cur, started=moment - 30, completed=moment - 10)
    evidence = gate.reconciliation_evidence(cur, now=NOW)
    assert evidence["state"] == "DRAINING", evidence
    return evidence


def behind(cur):
    """A worker ticking, completing, and losing to its own queue. Production today."""
    moment = _epoch()
    latch(cur, started=moment - 30, completed=moment - 10)
    cur.execute("INSERT INTO business_os_supplier_sync_jobs (id, connection_id, "
                "business_id, store_id, kind, resource_id, available_at) "
                "VALUES('job-1','conn','biz','store','inventory','r1',?)",
                (moment - (gate.CONFIRMATION_MAX_AGE_SECONDS + 1271),))
    evidence = gate.reconciliation_evidence(cur, now=NOW)
    assert evidence["state"] == gate.DRAIN_BEHIND, evidence
    return evidence


def stalled(cur):
    moment = _epoch()
    latch(cur, started=moment - 100000, completed=moment - 99000)
    evidence = gate.reconciliation_evidence(cur, now=NOW)
    assert evidence["state"] == "DRAIN_STALLED", evidence
    return evidence


def never_ran(cur):
    evidence = gate.reconciliation_evidence(cur, now=NOW)
    assert evidence["state"] == "NO_DRAIN_HAS_EVER_RUN", evidence
    return evidence


def name_for(cur, evidence, listing_id=DROPSHIP):
    """Classify a verdict the real gate produced. Never a literal dict.

    Returns both so a test can assert the name *and* the verdict it came from —
    a name alone could be right about a verdict that is wrong.
    """
    verdict = gate.evaluate(cur, listing_id=listing_id, evidence=evidence, now=NOW)
    return obs.classify(verdict), verdict


# ---------------------------------------------------------------------------
# §2 — the six canonical states, each from a verdict the gate really returned
# ---------------------------------------------------------------------------

def test_a_fresh_confirmation_under_a_healthy_latch_is_confirmed(cur):
    bind(cur)
    add_variant(cur)
    confirm(cur, age_seconds=60)
    name, verdict = name_for(cur, draining(cur))
    assert verdict["decision"] == gate.DECISION_ALLOW and verdict["unverified"] is False
    assert name == obs.CONFIRMED


def test_a_worker_that_never_ran_is_worker_stopped(cur):
    bind(cur)
    add_variant(cur)
    confirm(cur, age_seconds=60)
    name, verdict = name_for(cur, never_ran(cur))
    # Allowed — freshness cannot be demanded of a reconciler that never ran — and
    # the *reason* it was allowed is the thing being reported.
    assert verdict["decision"] == gate.DECISION_ALLOW and verdict["unverified"] is True
    assert name == obs.UNVERIFIED_WORKER_STOPPED


def test_a_worker_that_ran_and_died_is_drain_stalled_not_worker_stopped(cur):
    """Two different operator actions, so two different names.

    "Never deployed" is a configuration gap somebody has to go and fix; "ran and
    then stopped" is an incident with a start time. Collapsing them would send
    whoever is on call looking for the wrong thing — and the gate already draws
    this distinction, so collapsing it here would also be the reader disagreeing
    with the decider.
    """
    bind(cur)
    add_variant(cur)
    confirm(cur, age_seconds=60)
    name, verdict = name_for(cur, stalled(cur))
    assert verdict["decision"] == gate.DECISION_ALLOW and verdict["unverified"] is True
    assert name == obs.UNVERIFIED_DRAIN_STALLED
    assert name != obs.UNVERIFIED_WORKER_STOPPED


def test_a_reconciler_losing_to_its_queue_is_drain_behind(cur):
    """Production, 2026-10-03: this was 44 of 44 purchasable listings.

    The latch said a tick had started *and* completed, every source read SYNCED,
    and the queue's front was 3971 seconds overdue against a 2700-second
    tolerance. The staleness belonged to the backlog, not to any supplier — and
    this is the only state that says so.
    """
    bind(cur)
    add_variant(cur)
    confirm(cur, age_seconds=gate.CONFIRMATION_MAX_AGE_SECONDS + 600)
    name, verdict = name_for(cur, behind(cur))
    assert verdict["decision"] == gate.DECISION_ALLOW and verdict["unverified"] is True
    assert verdict["evidence_state"] == gate.DRAIN_BEHIND
    assert name == obs.UNVERIFIED_DRAIN_BEHIND


def test_a_confirmation_that_went_stale_under_a_healthy_latch_is_supplier_unconfirmed(cur):
    """The refusal that costs a sale because of what *we* do not know.

    A reconciler keeping up reached other listings and not this one, so this
    one's silence means something. This is the state the gate was written for,
    and it was zero in production — which is the only reason any non-zero value
    is alertable.
    """
    bind(cur)
    add_variant(cur)
    confirm(cur, age_seconds=gate.CONFIRMATION_MAX_AGE_SECONDS + 600)
    name, verdict = name_for(cur, draining(cur))
    assert verdict["decision"] == gate.DECISION_REFUSE
    assert verdict["reason"] == gate.REASON_STALE_CONFIRMATION
    assert name == obs.SUPPLIER_UNCONFIRMED


def test_every_active_variant_out_of_stock_is_an_affirmative_block(cur):
    """The supplier said "none left". Not an absence of reassurance — a statement."""
    bind(cur)
    add_variant(cur, stock_state=schema.STOCK_OUT_OF_STOCK)
    confirm(cur, age_seconds=60)
    name, verdict = name_for(cur, draining(cur))
    assert verdict["decision"] == gate.DECISION_REFUSE
    assert verdict["reason"] == gate.REASON_SOLD_OUT
    assert name == obs.AFFIRMATIVE_NEGATIVE_BLOCK


def test_a_failed_supplier_read_is_an_affirmative_block_not_an_unconfirmed_one(cur):
    """A read that was attempted and failed, which is positively bad news.

    The gate refuses this in the same tier as a sell-out and for the same reason:
    it is an affirmative statement that something is wrong, and it does not become
    less true because nothing is re-reading. Reporting it as
    ``SUPPLIER_UNCONFIRMED`` would file "the supplier is unreachable" under "we
    have not looked recently", and the two need different responses.
    """
    bind(cur, sync_state=schema.SYNC_ERROR)
    add_variant(cur)
    confirm(cur, age_seconds=60, sync_state=schema.SYNC_ERROR)
    name, verdict = name_for(cur, draining(cur))
    assert verdict["decision"] == gate.DECISION_REFUSE
    assert verdict["sync_state"] in gate.FAILED_SYNC_STATES
    assert name == obs.AFFIRMATIVE_NEGATIVE_BLOCK


def test_an_unreadable_confirmation_is_an_affirmative_block(cur):
    """Corrupt is not absent.

    Something wrote that column and nothing can read it — a failed write or a
    format drift between the writer and the parser. Waiting does not fix it, so
    it belongs with the other "something is broken" states rather than with the
    listings that are merely overdue for a look.
    """
    bind(cur)
    add_variant(cur)
    cur.execute("UPDATE marketplace_product_sources SET last_synced_at='not-a-date', "
                "sync_state=? WHERE listing_id=?", (schema.SYNC_SYNCED, DROPSHIP))
    name, verdict = name_for(cur, draining(cur))
    assert verdict["confirmation"] == gate.CONFIRMATION_UNREADABLE
    assert verdict["decision"] == gate.DECISION_REFUSE
    assert name == obs.AFFIRMATIVE_NEGATIVE_BLOCK


# ---------------------------------------------------------------------------
# §2 — the seventh fact, and why it is not folded into one of the six
# ---------------------------------------------------------------------------

def test_a_healthy_reconciler_that_has_not_reached_a_listing_gets_its_own_name(cur):
    """``CONFIRMATION_NEVER`` with a healthy latch is none of the six.

    This is the normal state of every listing for hours after a worker starts:
    the latch flips on tick #1 while confirmations arrive twenty jobs at a time,
    hourly. Two tempting places to file it, both wrong:

    ``UNVERIFIED_WORKER_STOPPED`` would put a worker outage on the owner's screen
    during every ordinary cold start — and once an alert has cried wolf through a
    deploy, it is ignored during the outage it was built for.

    ``CONFIRMED`` would claim a confirmation that does not exist, which is the
    exact inversion this whole module was written to prevent.

    So it is named. One extra word in the vocabulary is cheaper than either lie.
    """
    bind(cur)
    add_variant(cur)
    # No `confirm()` call: `last_synced_at` is NULL, which is what `link_source`
    # leaves — it has no parameter for that column, by design.
    evidence = draining(cur)
    name, verdict = name_for(cur, evidence)
    assert verdict["decision"] == gate.DECISION_ALLOW and verdict["unverified"] is True
    assert verdict["confirmation"] == gate.CONFIRMATION_NEVER
    assert verdict["evidence_state"] == "DRAINING"
    assert name == obs.UNVERIFIED_NOT_YET_REACHED
    assert name not in (obs.CONFIRMED, obs.UNVERIFIED_WORKER_STOPPED)
    # And it is still counted as exposure, because a sale here completes with no
    # confirmation behind it — the honest name must not become a quiet exemption.
    assert name in obs.UNVERIFIED_STATES


def test_a_merchant_stocked_listing_is_not_applicable_not_confirmed(cur):
    """"Nothing to check" and "checked and satisfied" must stay distinguishable.

    Counted rather than dropped, so the state counts sum to the catalogue and a
    reader can see which denominator they are reasoning about.
    """
    bind(cur, mode=schema.MODE_STOCKED)
    add_variant(cur)
    name, verdict = name_for(cur, draining(cur))
    assert verdict["decision"] == gate.NOT_APPLICABLE
    assert name == obs.NOT_APPLICABLE
    assert name not in obs.UNVERIFIED_STATES and name not in obs.REFUSING_STATES


def test_a_listing_with_no_supplier_at_all_is_not_applicable(cur):
    name, verdict = name_for(cur, draining(cur), listing_id=UNSOURCED)
    assert verdict["decision"] == gate.NOT_APPLICABLE
    assert name == obs.NOT_APPLICABLE


# ---------------------------------------------------------------------------
# §2 — the structural half: no verdict may go unnamed
# ---------------------------------------------------------------------------

def test_every_branch_of_evaluate_has_a_name():
    """Count the gate's return statements and the names this module can produce.

    The behavioural tests above cover every branch that exists *today*. This one
    fails when a new one appears: ``classify`` ends in a fallthrough — the
    ``UNVERIFIED_NOT_YET_REACHED`` default — so a new gate outcome would be
    silently absorbed into it and reported as a healthy reconciler that has not
    got around to this listing. That is the worst available default to inherit a
    new failure mode, and a count is the cheapest thing that notices.

    The nine, and where each is named above: two ``_not_applicable("no_supplier")``
    (a rejected reference and a listing with no source row — the first is a
    programmer-error path no lane can reach), ``_not_applicable("merchant_stocked")``,
    ``_refuse(SOLD_OUT)``, three ``_refuse(STALE_CONFIRMATION)`` (failed sync,
    unreadable stamp, overdue age — the first two are affirmative blocks here and
    only the third is ``SUPPLIER_UNCONFIRMED``), ``_allow(unverified=True)`` and
    ``_allow(unverified=False)``. The one unverified allow is what fans out into
    four reported states, by ``evidence_state``.
    """
    src = inspect.getsource(gate)
    evaluate = next(n for n in ast.parse(src).body
                    if isinstance(n, ast.FunctionDef) and n.name == "evaluate")
    returns = [n for n in ast.walk(evaluate) if isinstance(n, ast.Return) and n.value]
    assert len(returns) == 9, (
        "`evaluate` now has %d return statements, not the 9 this module was "
        "written against. A new outcome falls through `classify` into "
        "UNVERIFIED_NOT_YET_REACHED and is reported as a healthy reconciler. "
        "Name it in `classify` and add its test above." % len(returns))


def test_the_state_vocabulary_is_partitioned_not_overlapping():
    """A listing is exposed, refused, confirmed or none of our business — one of them.

    If any state appeared in both ``UNVERIFIED_STATES`` and ``REFUSING_STATES``
    the headline rates would double-count and could sum past 100%, which is the
    kind of number that gets a dashboard disbelieved wholesale.
    """
    assert not set(obs.UNVERIFIED_STATES) & set(obs.REFUSING_STATES)
    assert obs.CONFIRMED not in obs.UNVERIFIED_STATES
    assert obs.CONFIRMED not in obs.REFUSING_STATES
    assert obs.NOT_APPLICABLE not in obs.UNVERIFIED_STATES
    assert obs.NOT_APPLICABLE not in obs.REFUSING_STATES
    covered = set(obs.UNVERIFIED_STATES) | set(obs.REFUSING_STATES) | {
        obs.CONFIRMED, obs.NOT_APPLICABLE}
    assert covered == set(obs.CANONICAL_STATES)
    assert len(obs.CANONICAL_STATES) == len(set(obs.CANONICAL_STATES))


# ---------------------------------------------------------------------------
# §4 — percentiles that cannot flatter an empty measurement
# ---------------------------------------------------------------------------

def test_an_empty_sample_is_none_not_zero():
    """Zero seconds is the most reassuring possible rendering of "we did not look".

    A confirmation age of 0 reads as perfectly fresh. If every drop-ship source
    had a NULL ``last_synced_at`` — which was literally true of all 22 sources
    earlier in this subsystem's life — a zero here would have reported the
    catalogue as maximally healthy at the exact moment nothing had ever been
    confirmed.
    """
    for p in (50, 95, 99):
        assert obs.percentile([], p) is None
        assert obs.percentile([], p) != 0


def test_percentiles_are_nearest_rank_over_the_sorted_sample():
    values = [10, 20, 30, 40, 50, 60, 70, 80, 90, 100]
    assert obs.percentile(values, 50) == 50.0
    assert obs.percentile(values, 99) == 100.0
    assert obs.percentile(values, 0) == 10.0
    # Order of the input must not matter.
    assert obs.percentile(list(reversed(values)), 50) == 50.0
    # A single observation is its own every percentile, not an interpolation.
    assert obs.percentile([42], 95) == 42.0


def test_a_high_percentile_never_exceeds_the_sample():
    """p99 of a short sample must be a value that was observed, not an extrapolation.

    With 44 listings — production's purchasable count — a p99 computed by
    interpolation would report an age no listing has, and the one number an owner
    would quote in a decision is the tail.
    """
    values = [float(i) for i in range(44)]
    assert obs.percentile(values, 99) in values
    assert obs.percentile(values, 99) <= max(values)


# ---------------------------------------------------------------------------
# §5 — capacity, derived from the deployed constants
# ---------------------------------------------------------------------------

def test_the_projection_does_not_assume_a_bigger_worker():
    """§1: capacity is not to be tuned, so the study must model what is deployed.

    A projection that quietly assumed a larger ``limit`` would be describing a
    deployment nobody agreed to, and would make every oversubscription number
    below optimistic. Both constants are checked against their real sources:
    ``run_once``'s own default and the interval the Procfile passes.
    """
    default = inspect.signature(worker.run_once).parameters["limit"].default
    assert obs.DEPLOYED_TICK_LIMIT == default, (
        "worker.run_once's default limit is now %r; the capacity study is "
        "modelling %r." % (default, obs.DEPLOYED_TICK_LIMIT))
    procfile = os.path.join(os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__)))), "Procfile")
    if os.path.exists(procfile):
        with open(procfile, encoding="utf-8") as handle:
            text = handle.read()
        if "supplier_worker" in text:
            assert "--interval %d" % obs.DEPLOYED_TICK_SECONDS in text, (
                "the Procfile no longer deploys supplier_worker on a %ds "
                "interval" % obs.DEPLOYED_TICK_SECONDS)


def test_the_catalogue_cost_is_two_recurring_jobs_per_product():
    """``_seed_jobs`` creates one ``inventory`` and one ``product`` job per source.

    Both re-queue at their own cadence on success, so N products carry 2N
    recurring jobs for the life of the deployment. Measured in production: 196
    sources, 196 ``product`` jobs, 196 ``inventory`` jobs.
    """
    assert obs.capacity_projection(196)["recurring_jobs"] == 392
    assert obs.capacity_projection(0)["recurring_jobs"] == 0
    for kind in obs.PER_PRODUCT_JOB_KINDS:
        assert kind in worker.CADENCE


def test_capacity_is_read_from_cadence_not_restated(monkeypatch):
    """Halve the cadence and the demand must double. Nothing here is a literal.

    This is the test that makes the §5 numbers maintainable rather than a
    snapshot: ``CADENCE`` lives in the worker, and a module that copied its
    values would keep reporting a projection for a reconciler that had been
    retuned underneath it.
    """
    baseline = obs.capacity_projection(100)["demand_jobs_per_minute"]
    faster = {k: (v // 2 if k in obs.PER_PRODUCT_JOB_KINDS else v)
              for k, v in obs.CADENCE.items()}
    monkeypatch.setattr(obs, "CADENCE", faster)
    assert obs.capacity_projection(100)["demand_jobs_per_minute"] == pytest.approx(
        baseline * 2, rel=0.01)


def test_the_break_even_catalogue_is_solved_from_the_same_constants():
    """One number an owner can act on: the largest catalogue that keeps up.

    20 jobs per 300s is 4 jobs/minute. Each product demands one ``inventory``
    and one ``product`` job per 900s, so 2/900 per second. Break-even is
    therefore 30 products... and the measured answer is 48, because
    ``CADENCE["product"]`` is 3600, not 900. That discrepancy is exactly why this
    is solved from the imported cadence rather than from arithmetic in a comment.
    """
    n = obs.break_even_product_count()
    assert n > 0
    assert obs.capacity_projection(n)["keeps_up"] is True
    assert obs.capacity_projection(n + 1)["keeps_up"] is False
    # And a worker with no budget left keeps up with nothing at all, rather than
    # dividing by zero or reporting an infinite catalogue.
    assert obs.break_even_product_count(intents_per_tick=obs.DEPLOYED_TICK_LIMIT) == 0


def test_the_production_catalogue_is_oversubscribed_and_the_model_says_by_how_much():
    """196 products against a 4 jobs/minute reconciler.

    The model predicted a 5880-second full sweep; the measured p95 confirmation
    age was 6562s and the measured queue front was 3888s late. Predicting the
    observed order of magnitude is what licenses the larger projections below —
    without this assertion they would be arithmetic nobody had checked against
    reality.
    """
    p = obs.capacity_projection(196)
    assert p["capacity_jobs_per_minute"] == 4.0
    assert p["keeps_up"] is False
    assert p["oversubscription"] > 4.0
    assert 5000 < p["full_sweep_seconds"] < 7000
    # The consequence that matters: a full sweep slower than the freshness the
    # gate demands means every confirmation spends most of its life stale, so
    # DRAIN_BEHIND is latched permanently and the gate's strict branch is
    # unreachable. That is why §1's "DRAIN_BEHIND stays" is load-bearing rather
    # than a temporary allowance.
    assert p["within_gate_freshness"] is False
    assert p["gate_freshness_seconds"] == gate.CONFIRMATION_MAX_AGE_SECONDS


@pytest.mark.parametrize("products", [200, 500, 1000, 5000, 10000])
def test_growth_makes_it_monotonically_worse_and_never_catches_up(products):
    """§5's projection points. No catalogue above break-even recovers by waiting.

    Parametrised rather than asserted against five hard-coded ratios: the shape
    of the claim is "worse than the last one, and never within the gate's
    window", and pinning the exact multiples would turn a cadence change into
    five confusing failures instead of one informative one.
    """
    smaller = obs.capacity_projection(products // 2)
    bigger = obs.capacity_projection(products)
    assert bigger["oversubscription"] > smaller["oversubscription"]
    assert bigger["full_sweep_seconds"] > smaller["full_sweep_seconds"]
    assert bigger["keeps_up"] is False
    assert bigger["within_gate_freshness"] is False


def test_order_traffic_steals_inventory_freshness_from_the_same_budget():
    """``worker.py:280`` — ``for _ in range(limit - counts["intents"])``.

    The fulfilment outbox is drained first and sync jobs get what is left of the
    20. Production has no orders today, which is the *only* reason the catalogue
    is as fresh as it is; the same catalogue under order load refreshes strictly
    slower. A capacity study that modelled the two queues independently would
    predict a freshness that arrives only while nobody is buying anything.
    """
    idle = obs.capacity_projection(196, intents_per_tick=0)
    busy = obs.capacity_projection(196, intents_per_tick=10)
    assert busy["capacity_jobs_per_minute"] < idle["capacity_jobs_per_minute"]
    assert busy["oversubscription"] > idle["oversubscription"]
    assert busy["full_sweep_seconds"] > idle["full_sweep_seconds"]
    # Saturated by order traffic: no sync capacity at all, and said as None
    # rather than as a sweep time of zero.
    starved = obs.capacity_projection(196, intents_per_tick=obs.DEPLOYED_TICK_LIMIT)
    assert starved["capacity_jobs_per_minute"] == 0.0
    assert starved["keeps_up"] is False
    assert starved["full_sweep_seconds"] is None
    assert starved["oversubscription"] is None


def test_the_structural_coupling_between_the_two_queues_still_exists():
    """If the shared budget is ever split, ``intents_per_tick`` stops meaning this.

    Pinned against the worker's source because the parameter above models a line
    of code, not a policy — and a reader given a knob that no longer corresponds
    to anything would reason confidently about a system that had been fixed.
    """
    src = inspect.getsource(worker.run_once)
    assert 'range(limit - counts["intents"])' in src, (
        "run_once no longer takes sync-job budget out of the intent budget; "
        "capacity_projection's intents_per_tick models a coupling that is gone.")


# ---------------------------------------------------------------------------
# §3 — sustained conditions, and the one that must not page
# ---------------------------------------------------------------------------

def _snap(**kw):
    base = {"applicable": 1, "states": {}, "queue_overdue_seconds": 0.0}
    base.update(kw)
    return base


def test_a_condition_is_not_sustained_across_a_window_we_have_not_observed():
    """Fewer observations than required is False, not "close enough".

    The alternative fires on the first snapshot after every deploy, which is the
    moment an operator is least able to tell a real incident from a cold start.
    """
    always = lambda _: True
    assert obs.sustained([], always) is False
    assert obs.sustained([{}], always) is False
    assert obs.sustained([{}, {}], always) is False
    assert obs.sustained([{}, {}, {}], always) is True


def test_one_recovered_observation_breaks_the_streak():
    """A blip in the middle of the window is not a sustained condition.

    Only the last ``required`` observations count, so a condition that was true,
    recovered, and is true again has not held — which is the whole difference
    between "sustained" and "has happened recently".
    """
    flag = lambda s: bool(s.get("bad"))
    history = [{"bad": True}, {"bad": False}, {"bad": True}]
    assert obs.sustained(history, flag) is False
    assert obs.sustained(history + [{"bad": True}, {"bad": True}], flag) is True
    # And an older failure outside the window does not keep it suppressed.
    assert obs.sustained([{"bad": False}] + [{"bad": True}] * 3, flag) is True


def test_sustained_keeps_no_state_between_calls():
    """No module state, no clock, no timer — the reservation-expiry lesson.

    Anything that remembered its own condition would forget on restart and could
    not be reasoned about across two workers. Called twice with the same history,
    this must answer the same thing.
    """
    history = [{"bad": True}] * 3
    flag = lambda s: bool(s.get("bad"))
    assert obs.sustained(history, flag) is obs.sustained(history, flag) is True
    assert obs.sustained([{"bad": False}] * 3, flag) is False


def test_the_catalogue_wide_drain_behind_does_not_page_anyone():
    """100% ``UNVERIFIED_DRAIN_BEHIND``, sustained, and still not an alert.

    This is the threshold decision of §3, and it is derived rather than picked.
    Production has been in this state for days; §1 forbids tuning the capacity
    that would change it. So it is a decision already made, and an alert on it
    would be permanently on — at which point it stops being read, and the
    conditions that *are* news get buried underneath it.

    Reported as a standing condition (``exposure_complete``) so that the day it
    changes, somebody can see it. Not reported as an incident.
    """
    history = [_snap(applicable=44, states={obs.UNVERIFIED_DRAIN_BEHIND: 44})] * 5
    alerts = obs.alert_conditions(history)
    assert alerts["refusing"] is False
    assert alerts["worker_stopped"] is False
    assert alerts["backlog_growing"] is False
    # Visible, but as a fact about the deployment rather than a page.
    assert alerts["exposure_complete"] is True


def test_a_single_refused_buyer_is_news_once_it_persists():
    """Refusals were zero in production, which is what makes any value a change."""
    bad = _snap(applicable=44, states={obs.SUPPLIER_UNCONFIRMED: 1,
                                       obs.UNVERIFIED_DRAIN_BEHIND: 43})
    assert obs.alert_conditions([bad] * 3)["refusing"] is True
    # One snapshot is not a condition — a listing mid-revision can refuse for a
    # single tick and recover on the next.
    assert obs.alert_conditions([bad])["refusing"] is False


def test_a_stopped_worker_alerts_but_being_behind_does_not():
    """Behind still produces confirmations, just too slowly. Stopped produces none.

    The pair of assertions is the point: if ``worker_stopped`` were keyed on
    anything that DRAIN_BEHIND also satisfies, it would be true in production
    today and the real outage would be indistinguishable from the steady state.
    """
    stopped = _snap(applicable=44, states={obs.UNVERIFIED_WORKER_STOPPED: 44})
    behind_only = _snap(applicable=44, states={obs.UNVERIFIED_DRAIN_BEHIND: 44})
    assert obs.alert_conditions([stopped] * 3)["worker_stopped"] is True
    assert obs.alert_conditions([_snap(applicable=44,
                                       states={obs.UNVERIFIED_DRAIN_STALLED: 44})] * 3
                                )["worker_stopped"] is True
    assert obs.alert_conditions([behind_only] * 3)["worker_stopped"] is False


def test_a_growing_backlog_is_distinguished_from_one_that_is_draining():
    """The only condition that cannot be read off a single snapshot.

    Strictly increasing, not "higher than the first": a queue that spiked once
    and has been draining ever since is recovering, and reporting that as growth
    would alert on the good case — which is how an operator learns to ignore the
    signal that distinguishes a bounded oversubscription from a runaway one.
    """
    growing = [_snap(queue_overdue_seconds=v) for v in (1000, 2000, 3000)]
    assert obs.alert_conditions(growing)["backlog_growing"] is True

    draining_queue = [_snap(queue_overdue_seconds=v) for v in (9000, 5000, 1000)]
    assert obs.alert_conditions(draining_queue)["backlog_growing"] is False

    # Oversubscribed but bounded — production's actual shape, where the front of
    # the queue oscillates around a ceiling instead of diverging.
    oscillating = [_snap(queue_overdue_seconds=v) for v in (3900, 3700, 3971)]
    assert obs.alert_conditions(oscillating)["backlog_growing"] is False

    # A flat backlog is not growth either, even a large one.
    assert obs.alert_conditions([_snap(queue_overdue_seconds=4000)] * 3
                                )["backlog_growing"] is False


def test_alert_conditions_reports_how_much_history_it_had():
    """So a reader can tell "no alerts" from "not enough observations to say"."""
    alerts = obs.alert_conditions([_snap()])
    assert alerts["observations"] == 1
    assert alerts["required"] == obs.SUSTAINED_OBSERVATIONS
    assert obs.SUSTAINED_OBSERVATIONS >= 2, (
        "a single observation cannot establish a sustained condition")


# ---------------------------------------------------------------------------
# §2/§4 — the snapshot
# ---------------------------------------------------------------------------

def test_the_snapshot_denominator_is_what_could_actually_be_bought(cur):
    """196 published listings, 44 purchasable. The rate must use the 44.

    A percentage over the published count would have reported a catalogue-wide
    exposure as a fifth of one — the single most consequential arithmetic
    decision in this module, and the one that is invisible once the number is on
    a screen.
    """
    bind(cur)
    add_variant(cur)
    confirm(cur, age_seconds=gate.CONFIRMATION_MAX_AGE_SECONDS + 600)
    behind(cur)
    out = obs.snapshot(cur, now=NOW)
    # UNBUYABLE is published, priced at zero, quantity zero — excluded.
    assert out["examined"] == 4, out["states"]
    assert sum(out["states"].values()) == out["examined"]


def test_the_snapshot_reproduces_the_production_shape(cur):
    """Two drop-ship listings behind the queue, two listings this gate skips.

    Asserted through ``snapshot`` rather than through ``classify`` so the numbers
    an owner reads come from the shipped code path, not from a probe script that
    happened to agree with it once.
    """
    for listing_id in (DROPSHIP, DROPSHIP_B):
        bind(cur, listing_id=listing_id)
        add_variant(cur, listing_id=listing_id)
        confirm(cur, listing_id=listing_id,
                age_seconds=gate.CONFIRMATION_MAX_AGE_SECONDS + 600)
    bind(cur, listing_id=STOCKED, mode=schema.MODE_STOCKED)
    behind(cur)

    out = obs.snapshot(cur, now=NOW)
    assert out["latch_state"] == gate.DRAIN_BEHIND
    assert out["latch_running"] is False
    assert out["queue_overdue_seconds"] > gate.CONFIRMATION_MAX_AGE_SECONDS
    assert out["applicable"] == 2
    assert out["states"][obs.UNVERIFIED_DRAIN_BEHIND] == 2
    assert out["states"][obs.NOT_APPLICABLE] == 2
    assert out["unverified"] == 2 and out["refusing"] == 0
    assert out["drain_behind_rate"] == 1.0
    assert out["unverified_rate"] == 1.0
    assert out["refusal_rate"] == 0.0
    age = out["confirmation_age"]
    assert age["n"] == 2
    assert age["p50"] > gate.CONFIRMATION_MAX_AGE_SECONDS
    assert age["over_gate_max"] == 2
    assert age["gate_max_seconds"] == gate.CONFIRMATION_MAX_AGE_SECONDS


def test_rates_are_none_when_there_is_no_denominator(cur):
    """A catalogue with nothing applicable reports None, not a healthy 0.0.

    ``0.0`` means "we measured, and none of them are exposed". This situation is
    "there was nothing to measure", and the two must not render identically — a
    deployment that lost its supplier rows would otherwise look like the safest
    one on record.
    """
    out = obs.snapshot(cur, now=NOW)
    assert out["applicable"] == 0
    assert out["drain_behind_rate"] is None
    assert out["unverified_rate"] is None
    assert out["refusal_rate"] is None
    assert out["confirmation_age"]["p50"] is None
    assert out["confirmation_age"]["n"] == 0


def test_the_latch_is_read_once_so_every_listing_describes_the_same_moment(cur):
    """A snapshot whose listings disagreed about the worker would describe no moment.

    Re-reading the latch per listing is both slower and wrong: a worker that
    stalls mid-scan would produce a report that is half one world and half
    another, and the mixture is not a state the system was ever in.
    """
    for listing_id in (DROPSHIP, DROPSHIP_B):
        bind(cur, listing_id=listing_id)
        add_variant(cur, listing_id=listing_id)
        confirm(cur, listing_id=listing_id, age_seconds=60)
    draining(cur)

    calls = {"n": 0}
    real = gate.reconciliation_evidence

    def counted(*args, **kwargs):
        calls["n"] += 1
        return real(*args, **kwargs)

    gate.reconciliation_evidence = counted
    try:
        out = obs.snapshot(cur, now=NOW)
    finally:
        gate.reconciliation_evidence = real
    assert calls["n"] == 1, (
        "the latch was read %d times for %d listings" % (calls["n"], out["applicable"]))
    assert out["states"][obs.CONFIRMED] == 2


def test_the_snapshot_writes_nothing(cur):
    """No decision, no write, no provider call — SELECTs and arithmetic.

    Checked by comparing every row of both tables the gate reads, before and
    after. A reporting path that mutated the thing it reports would make the
    report a cause of the state it describes.
    """
    bind(cur)
    add_variant(cur)
    confirm(cur, age_seconds=60)
    behind(cur)

    def dump():
        snapshot_rows = {}
        for table in ("marketplace_product_sources", "marketplace_listing_variants",
                      "marketplace_listings", "business_os_supplier_sync_jobs",
                      "business_os_supplier_drain_ticks"):
            cur.execute("SELECT * FROM %s" % table)
            snapshot_rows[table] = [tuple(row) for row in (cur.fetchall() or [])]
        return snapshot_rows

    before = dump()
    obs.snapshot(cur, now=NOW)
    assert dump() == before


def test_the_snapshot_can_be_scoped_to_explicit_listings(cur):
    """So a caller can ask about one store without reading the whole catalogue."""
    for listing_id in (DROPSHIP, DROPSHIP_B):
        bind(cur, listing_id=listing_id)
        add_variant(cur, listing_id=listing_id)
        confirm(cur, listing_id=listing_id, age_seconds=60)
    draining(cur)
    out = obs.snapshot(cur, listing_ids=[DROPSHIP], now=NOW)
    assert out["examined"] == 1 and out["applicable"] == 1
    assert out["states"][obs.CONFIRMED] == 1


def test_the_snapshot_survives_a_deployment_without_the_supplier_subsystem(cur):
    """No latch table, no job table — the gate fails closed and so must the reader.

    ``reconciliation_evidence`` catches every exception and reports "never
    drained", which is correct for a deployment that has not initialised the
    supplier subsystem. The reader must not be the thing that raises instead.
    """
    cur.execute("DROP TABLE business_os_supplier_drain_ticks")
    cur.execute("DROP TABLE business_os_supplier_sync_jobs")
    bind(cur)
    add_variant(cur)
    confirm(cur, age_seconds=60)
    out = obs.snapshot(cur, now=NOW)
    assert out["latch_state"] == "NO_DRAIN_HAS_EVER_RUN"
    assert out["latch_running"] is False
    assert out["queue_overdue_seconds"] == 0.0
    assert out["states"][obs.UNVERIFIED_WORKER_STOPPED] == 1


def test_the_reader_imports_the_gate_rather_than_restating_its_rules():
    """Structural: no freshness window, no latch name and no cadence as a literal.

    The gate's docstrings warn against a second authority on the same question.
    A reporting layer is the easiest place to acquire one by accident — copying
    ``2700`` into a dashboard is a one-character decision — and the resulting
    disagreement appears only as a number nobody can reconcile.
    """
    src = inspect.getsource(obs)
    assert "import marketplace_supplier_checkout as gate" in src
    assert "from services.business_os.suppliers.worker import CADENCE" in src
    for literal in (str(gate.CONFIRMATION_MAX_AGE_SECONDS),
                    str(worker.CADENCE["inventory"]),
                    str(worker.CADENCE["product"])):
        assert literal not in src.replace("2026-10-03", ""), (
            "%s appears as a literal; it must come from the module that owns it"
            % literal)


# --------------------------------------------------------------------------
# The denominator (§5). Two populations, and the wrong one is reassuring.
# --------------------------------------------------------------------------

#: Production, 2026-10-03. Both measured, both real, and they answer different
#: questions: 44 listings are *purchasable* (published, in stock, priced) and so
#: form the gate's exposure denominator; 196 products are *sourced* and so form
#: the reconciler's demand denominator. The reconciler refreshes all 196
#: regardless of whether any of them can be bought.
MEASURED_PURCHASABLE_LISTINGS = 44
MEASURED_SOURCED_PRODUCTS = 196


def test_the_two_denominators_invert_the_verdict_so_they_cannot_be_swapped():
    """The exposure count does not merely understate the load — it flips it.

    This is the trap worth a test rather than a comment. ``snapshot()`` puts an
    ``applicable`` integer in the caller's hand, it is the obvious thing to
    reach for, and feeding it to the projection returns ``keeps_up=True``: the
    single most reassuring output the function can produce, at the moment the
    queue is in fact 4x oversubscribed with 277 of 395 jobs overdue.

    Pinned via ``break_even_product_count``, which is solved from ``CADENCE``,
    so this stays an assertion about the deployed worker rather than about two
    numbers someone typed. The verdict inverts precisely because break-even
    falls *between* the two populations.
    """
    break_even = obs.break_even_product_count()
    assert MEASURED_PURCHASABLE_LISTINGS <= break_even < MEASURED_SOURCED_PRODUCTS, (
        "break-even is %d, which no longer separates the exposure count (%d) from "
        "the demand count (%d) -- the inversion this test pins has moved, and the "
        "capacity_projection docstring's worked example is now wrong"
        % (break_even, MEASURED_PURCHASABLE_LISTINGS, MEASURED_SOURCED_PRODUCTS))

    exposure = obs.capacity_projection(MEASURED_PURCHASABLE_LISTINGS)
    demand = obs.capacity_projection(MEASURED_SOURCED_PRODUCTS)
    assert exposure["keeps_up"] is True
    assert demand["keeps_up"] is False
    assert exposure["oversubscription"] < 1.0 < demand["oversubscription"]
    # And the consequence the gate actually cares about: over the real
    # population a full sweep cannot meet the freshness the gate demands, which
    # is why DRAIN_BEHIND is latched permanently rather than intermittently.
    assert exposure["within_gate_freshness"] is True
    assert demand["within_gate_freshness"] is False


def test_the_demand_population_is_counted_from_the_queue_not_the_catalogue(cur):
    """Distinct products over the per-product kinds, ignoring everything else.

    Seeded with the shape production actually has: every product carrying both
    recurring kinds, plus the per-connection singletons (``health``, ``shops``)
    that are not per-product and must not inflate the count.
    """
    moment = _epoch()
    for n in range(7):
        for kind in obs.PER_PRODUCT_JOB_KINDS:
            cur.execute(
                "INSERT INTO business_os_supplier_sync_jobs (id, connection_id, "
                "business_id, store_id, kind, resource_id, available_at) "
                "VALUES(?,'conn','biz','store',?,?,?)",
                (f"job-{kind}-{n}", kind, f"prod-{n}", moment),
            )
    for kind in ("health", "shops"):
        cur.execute(
            "INSERT INTO business_os_supplier_sync_jobs (id, connection_id, "
            "business_id, store_id, kind, resource_id, available_at) "
            "VALUES(?,'conn','biz','store',?,'whole-shop',?)",
            (f"job-{kind}", kind, moment),
        )

    assert obs.catalogue_demand_population(cur) == 7
    # 7 products x 2 kinds = 14 recurring jobs; the 2 singletons are not the
    # projection's business, and counting them would overstate a catalogue.
    assert obs.measured_capacity(cur)["recurring_jobs"] == 14


def test_an_unreadable_queue_is_none_rather_than_a_comfortable_zero(cur):
    """Because ``capacity_projection(0)`` reports a healthy worker.

    The substitution is a single ``or 0`` away and produces the same false
    reassurance as a percentile of zero over an empty sample. Asserted together
    so the comfortable answer is visible next to the honest one.
    """
    assert obs.capacity_projection(0)["keeps_up"] is True

    cur.execute("DROP TABLE business_os_supplier_sync_jobs")
    assert obs.catalogue_demand_population(cur) is None

    out = obs.measured_capacity(cur)
    assert out["measured"] is False
    assert out["product_count"] is None
    assert "keeps_up" not in out, (
        "a verdict was reported for a population that could not be read")


def test_an_empty_queue_is_zero_rather_than_unreadable(cur):
    """The other half: zero sourced products is a real, reportable answer.

    ``None`` and ``0`` must not collapse in either direction -- an empty queue
    genuinely means the reconciler has nothing to do.
    """
    assert obs.catalogue_demand_population(cur) == 0
    out = obs.measured_capacity(cur)
    assert out["measured"] is True
    assert out["product_count"] == 0


def test_measured_capacity_is_the_projection_over_the_counted_population(cur):
    """No second arithmetic path. It composes, it does not re-derive."""
    moment = _epoch()
    for n in range(3):
        for kind in obs.PER_PRODUCT_JOB_KINDS:
            cur.execute(
                "INSERT INTO business_os_supplier_sync_jobs (id, connection_id, "
                "business_id, store_id, kind, resource_id, available_at) "
                "VALUES(?,'conn','biz','store',?,?,?)",
                (f"job-{kind}-{n}", kind, f"prod-{n}", moment),
            )
    measured = obs.measured_capacity(cur, intents_per_tick=5)
    expected = obs.capacity_projection(3, intents_per_tick=5)
    expected["measured"] = True
    assert measured == expected
