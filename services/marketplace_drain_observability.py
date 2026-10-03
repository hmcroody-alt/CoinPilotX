"""The read side of the supplier checkout gate: what is it deciding, and why.

``marketplace_supplier_checkout`` decides, per checkout, whether a drop-shipped
listing may be charged for. It does that well and it says why in its audit
annotation — but only to the one caller holding the verdict. Nothing anywhere
reports the *distribution*: how much of the catalogue is confirmed, how much is
being allowed through unverified, and which of several very different reasons is
responsible. An owner asking "is the money path healthy?" had no way to look.

That gap is not hypothetical. Measured in production on 2026-10-03, every single
one of the 44 purchasable drop-ship listings was in ``UNVERIFIED_DRAIN_BEHIND``
— the gate was allowing 100% of drop-ship checkouts without a fresh supplier
confirmation — and the only evidence of it anywhere was a latch value computed
and discarded inside each request.

Two rules this module follows, because getting either wrong would make it worse
than nothing:

*It never re-decides.* Every state here is derived from the verdict
:func:`marketplace_supplier_checkout.evaluate` already returned. A reporting
layer that re-implemented the rules would eventually disagree with the gate, and
then the dashboard would be confidently describing a system that does not exist.
The gate's own docstrings warn against a second authority on the same question;
this is a reader, not a second opinion.

*It never widens anything.* There is no decision here, no write, no provider
call. :func:`snapshot` is SELECTs and arithmetic.

On the vocabulary
-----------------
The owner named six canonical states. Five map exactly. The sixth case —
``UNVERIFIED_NOT_YET_REACHED`` — is a seventh fact that the six cannot express,
and it is reported under its own name rather than folded into a neighbour. See
:data:`CANONICAL_STATES` for why that matters.
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping, Sequence

from services import marketplace_supplier_checkout as gate
from services.business_os.suppliers.worker import CADENCE

# --------------------------------------------------------------------------
# The vocabulary
# --------------------------------------------------------------------------

CONFIRMED = "CONFIRMED"
UNVERIFIED_WORKER_STOPPED = "UNVERIFIED_WORKER_STOPPED"
UNVERIFIED_DRAIN_STALLED = "UNVERIFIED_DRAIN_STALLED"
UNVERIFIED_DRAIN_BEHIND = "UNVERIFIED_DRAIN_BEHIND"
SUPPLIER_UNCONFIRMED = "SUPPLIER_UNCONFIRMED"
AFFIRMATIVE_NEGATIVE_BLOCK = "AFFIRMATIVE_NEGATIVE_BLOCK"

#: The reconciler is running *and keeping up*, and this particular listing has
#: simply not been reached yet — ``CONFIRMATION_NEVER`` with a healthy latch.
#:
#: Not one of the six, and deliberately not folded into any of them. It is the
#: normal state of every listing for the first hours after a worker starts,
#: because the latch flips on tick #1 while confirmations arrive twenty jobs at
#: a time. Reporting it as ``UNVERIFIED_WORKER_STOPPED`` would put a false
#: worker outage on the owner's screen during an ordinary cold start, and
#: reporting it as ``CONFIRMED`` would claim a confirmation that does not exist.
#: Both of those are worse than one extra name.
UNVERIFIED_NOT_YET_REACHED = "UNVERIFIED_NOT_YET_REACHED"

#: Listings this gate has no opinion about: no supplier, or merchant-stocked.
#: Counted and reported rather than dropped, so the state counts sum to the
#: catalogue and a reader can see the denominator they are reasoning about.
NOT_APPLICABLE = "NOT_APPLICABLE"

#: Ordered worst-understood-last, which is the order a reader wants: the two
#: refusals first because they cost a sale right now, then the unverified
#: allows, which cost nothing today and are the ones that will surprise someone
#: later.
CANONICAL_STATES = (
    AFFIRMATIVE_NEGATIVE_BLOCK,
    SUPPLIER_UNCONFIRMED,
    UNVERIFIED_DRAIN_BEHIND,
    UNVERIFIED_DRAIN_STALLED,
    UNVERIFIED_WORKER_STOPPED,
    UNVERIFIED_NOT_YET_REACHED,
    CONFIRMED,
    NOT_APPLICABLE,
)

#: States in which a sale completes with no fresh supplier confirmation behind
#: it. Named as a set because "how exposed are we right now" is the question
#: this module exists to answer, and it is a sum over four states rather than a
#: single counter.
UNVERIFIED_STATES = (
    UNVERIFIED_DRAIN_BEHIND,
    UNVERIFIED_DRAIN_STALLED,
    UNVERIFIED_WORKER_STOPPED,
    UNVERIFIED_NOT_YET_REACHED,
)

#: States that refuse a buyer. Both cost a sale; they differ in whether anything
#: is wrong with the *item* (affirmative) or with our *knowledge* of it.
REFUSING_STATES = (AFFIRMATIVE_NEGATIVE_BLOCK, SUPPLIER_UNCONFIRMED)


def classify(verdict: Mapping[str, Any]) -> str:
    """Name the verdict :func:`gate.evaluate` returned. Derivation, not decision.

    Reads only keys the gate guarantees on every answer, so this cannot raise on
    a refusal that carries less than an allow — the gate is explicit that it
    gathers the same four facts for both, and this depends on that.
    """
    decision = verdict.get("decision")
    if decision == gate.NOT_APPLICABLE:
        return NOT_APPLICABLE

    if decision == gate.DECISION_ALLOW:
        if not verdict.get("unverified"):
            return CONFIRMED
        evidence = verdict.get("evidence_state")
        if evidence == gate.DRAIN_BEHIND:
            return UNVERIFIED_DRAIN_BEHIND
        if evidence in ("DRAIN_STALLED", "TICKING_BUT_NOT_COMPLETING"):
            # Both mean "it ran and then stopped producing", which is a
            # different operator action from "it was never deployed" — the
            # first is an incident, the second is a configuration gap.
            return UNVERIFIED_DRAIN_STALLED
        if evidence == "NO_DRAIN_HAS_EVER_RUN":
            return UNVERIFIED_WORKER_STOPPED
        # Latch healthy, listing unreached. See UNVERIFIED_NOT_YET_REACHED.
        return UNVERIFIED_NOT_YET_REACHED

    # A refusal. Which kind matters more than the fact: one is the supplier
    # telling us something is wrong, the other is us not knowing.
    if verdict.get("reason") == gate.REASON_SOLD_OUT:
        return AFFIRMATIVE_NEGATIVE_BLOCK
    if str(verdict.get("sync_state") or "").strip().upper() in gate.FAILED_SYNC_STATES:
        return AFFIRMATIVE_NEGATIVE_BLOCK
    if verdict.get("confirmation") == gate.CONFIRMATION_UNREADABLE:
        # A write that went wrong, not an absence of one. The gate refuses it for
        # the same reason it refuses a failed sync: something is broken and
        # waiting will not fix it.
        return AFFIRMATIVE_NEGATIVE_BLOCK
    return SUPPLIER_UNCONFIRMED


# --------------------------------------------------------------------------
# Percentiles
# --------------------------------------------------------------------------

def percentile(values: Sequence[float], p: float) -> float | None:
    """Nearest-rank percentile. ``None`` for an empty sample, never 0.

    Zero would be indistinguishable from "every confirmation is perfectly
    fresh", which is the most reassuring possible reading of "we measured
    nothing" — exactly the inversion this module is meant to prevent.
    """
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])
    rank = int(round(p / 100.0 * (len(ordered) - 1)))
    return float(ordered[max(0, min(len(ordered) - 1, rank))])


# --------------------------------------------------------------------------
# Capacity
# --------------------------------------------------------------------------

#: Jobs a tick may read, from ``worker.run_once``'s own default, and the tick
#: period the Procfile actually deploys (``supplier_worker.py --interval 300``).
#:
#: Both are arguments rather than module constants upstream, so they are
#: parameters here too — a projection that hard-coded them would keep answering
#: for a deployment that had been changed. The defaults are what production runs
#: today, which is the only reason they are defaults.
DEPLOYED_TICK_LIMIT = 20
DEPLOYED_TICK_SECONDS = 300

#: Recurring job kinds per drop-ship product. ``worker._seed_jobs`` creates one
#: ``inventory`` and one ``product`` job per source, and both re-queue at their
#: own cadence on success, so a catalogue of N products carries 2N recurring
#: jobs forever. Measured in production: 196 sources, 196 ``product`` jobs, 196
#: ``inventory`` jobs.
PER_PRODUCT_JOB_KINDS = ("inventory", "product")


def capacity_projection(product_count: int, *, tick_limit: int = DEPLOYED_TICK_LIMIT,
                        tick_seconds: int = DEPLOYED_TICK_SECONDS,
                        intents_per_tick: int = 0) -> dict:
    """What a catalogue of ``product_count`` products costs the reconciler.

    ``product_count`` is a **demand** population: how many products the
    reconciler owes recurring work for. It is *not* the number of listings a
    buyer can see. Use :func:`catalogue_demand_population` to obtain it, or
    :func:`measured_capacity` to avoid choosing at all.

    This distinction is the whole reason the paragraph exists, because the
    wrong denominator does not produce an obviously broken answer — it produces
    a *reassuring* one. On 2026-10-03 production held 44 purchasable drop-ship
    listings and 196 sourced products. Those are different populations
    measuring different things: 44 is the gate's **exposure** denominator, the
    one :func:`snapshot` divides its rates by, and it counts only listings that
    are published, in stock and priced. The reconciler does not care about any
    of that — it refreshes every sourced product, purchasable or not.

    Passing the exposure count in anyway returns ``keeps_up=True`` at 0.92×
    oversubscription, i.e. "the worker is comfortably ahead". The demand count
    returns ``keeps_up=False`` at 4.08× and a 5880s full sweep. The second is
    the truth, and it is corroborated twice over by measurement that does not
    share the model's assumptions: :func:`snapshot` observed a p95 confirmation
    age of 8400s in production, and the job table's own oldest-overdue spread
    was 8778s and 11454s. A reader who reached for ``snapshot()["applicable"]``
    because it was the integer closest to hand would have had every reason to
    believe the queue was healthy while 277 of 395 jobs sat overdue.

    Every number is derived from :data:`CADENCE` — imported, not restated — so a
    cadence change moves this projection instead of silently invalidating it.
    Nothing here proposes a larger ``tick_limit``: the brief is explicit that
    capacity is not to be tuned, and a study that quietly assumed a fix would be
    describing a deployment nobody agreed to.

    ``intents_per_tick`` is not decoration. ``run_once`` drains the fulfilment
    outbox first and then reads sync jobs ``for _ in range(limit -
    counts["intents"])`` — so order traffic and inventory freshness come out of
    one shared budget of 20. Production has no orders today, which is the only
    reason the catalogue is as fresh as it is; the same catalogue under load
    refreshes strictly slower, and this parameter is how that is expressed
    rather than discovered later.
    """
    products = max(0, int(product_count))
    budget = max(0, int(tick_limit) - max(0, int(intents_per_tick)))
    # Jobs the worker can retire per second, at the deployed cadence.
    capacity_per_second = (budget / float(tick_seconds)) if tick_seconds > 0 else 0.0
    # Jobs the catalogue generates per second: one per kind per cadence.
    demand_per_second = sum(products / float(CADENCE[kind])
                            for kind in PER_PRODUCT_JOB_KINDS)
    recurring_jobs = products * len(PER_PRODUCT_JOB_KINDS)
    sweep_seconds = (recurring_jobs / capacity_per_second) if capacity_per_second else None
    return {
        "product_count": products,
        "recurring_jobs": recurring_jobs,
        "capacity_jobs_per_minute": round(capacity_per_second * 60, 2),
        "demand_jobs_per_minute": round(demand_per_second * 60, 2),
        "oversubscription": (round(demand_per_second / capacity_per_second, 2)
                             if capacity_per_second else None),
        "keeps_up": bool(capacity_per_second and demand_per_second <= capacity_per_second),
        # How long one full pass over the catalogue takes, which is also the
        # worst confirmation age the catalogue can hold in steady state.
        "full_sweep_seconds": None if sweep_seconds is None else round(sweep_seconds),
        # The single fact that decides whether the gate's strict branch is
        # reachable at all: if a full sweep is slower than the freshness the
        # gate demands, every confirmation spends most of its life stale and
        # DRAIN_BEHIND is permanently latched.
        "within_gate_freshness": bool(
            sweep_seconds is not None
            and sweep_seconds <= gate.CONFIRMATION_MAX_AGE_SECONDS),
        "gate_freshness_seconds": gate.CONFIRMATION_MAX_AGE_SECONDS,
    }


def break_even_product_count(*, tick_limit: int = DEPLOYED_TICK_LIMIT,
                             tick_seconds: int = DEPLOYED_TICK_SECONDS,
                             intents_per_tick: int = 0) -> int:
    """The largest catalogue the deployed reconciler can actually keep up with.

    Solved from the same constants rather than searched, and reported as its own
    number because it is the one an owner can act on: below it the gate's
    freshness demand is meetable, above it the queue diverges and no amount of
    waiting catches up.
    """
    budget = max(0, int(tick_limit) - max(0, int(intents_per_tick)))
    if budget <= 0 or tick_seconds <= 0:
        return 0
    capacity_per_second = budget / float(tick_seconds)
    per_product = sum(1.0 / float(CADENCE[kind]) for kind in PER_PRODUCT_JOB_KINDS)
    return int(capacity_per_second / per_product) if per_product else 0


#: The reconciler's own queue. Stated here as a literal because
#: ``services.business_os.suppliers.worker`` inlines it too and exports no
#: constant to import — so this is a second mention, not a second authority. If
#: the table is ever renamed, :func:`catalogue_demand_population` returns
#: ``None`` rather than a wrong number, and the tests below say so.
SYNC_JOBS_TABLE = "business_os_supplier_sync_jobs"


def catalogue_demand_population(cur) -> int | None:
    """How many products the reconciler owes recurring work for. SELECT only.

    This is the denominator :func:`capacity_projection` wants, read from the
    queue the worker actually services rather than inferred from anything
    buyer-facing. Counting distinct ``resource_id`` over
    :data:`PER_PRODUCT_JOB_KINDS` ties the number to the same constant the
    projection multiplies by, so the two cannot drift apart: add a third
    per-product kind and both move together.

    Returns ``None`` — not ``0`` — when the table is absent or unreadable.
    Zero is a real and reportable answer ("no sourced products"), and
    collapsing "I cannot see the queue" onto it would tell an owner the
    reconciler has nothing to do at the exact moment nobody can tell. The same
    inversion the drain states exist to keep apart.
    """
    placeholders = ",".join("?" for _ in PER_PRODUCT_JOB_KINDS)
    try:
        cur.execute(
            f"SELECT COUNT(DISTINCT resource_id) AS n FROM {SYNC_JOBS_TABLE} "
            f"WHERE kind IN ({placeholders})",
            tuple(PER_PRODUCT_JOB_KINDS),
        )
        row = cur.fetchone()
    except Exception:
        return None
    if row is None:
        return None
    # dict(row), never iteration: a row yields VALUES on SQLite and NAMES on
    # PostgreSQL, so `list(row)[0]` would return the string "n" in production.
    try:
        return int(dict(row)["n"] or 0)
    except Exception:
        return None


def measured_capacity(cur, *, intents_per_tick: int = 0) -> dict:
    """:func:`capacity_projection` over the population actually in the queue.

    Exists so that the common case requires no choice of denominator. A caller
    that wants "is the reconciler keeping up with what we have?" should reach
    for this; ``capacity_projection`` stays available for the hypotheticals the
    brief asks for (200/500/1k/5k/10k), which are by definition not measured.

    When the population cannot be read, the projection is omitted rather than
    computed from a substituted zero — ``capacity_projection(0)`` reports
    ``keeps_up=True``, which is exactly the false reassurance this module is
    trying to stop emitting.
    """
    products = catalogue_demand_population(cur)
    if products is None:
        return {
            "product_count": None,
            "measured": False,
            "reason": f"{SYNC_JOBS_TABLE} absent or unreadable",
        }
    projection = capacity_projection(products, intents_per_tick=intents_per_tick)
    projection["measured"] = True
    return projection


# --------------------------------------------------------------------------
# Sustained conditions
# --------------------------------------------------------------------------

#: How many consecutive observations a condition must hold before it is worth
#: anyone's attention. The reconciler ticks every 300s, so three observations is
#: a condition that has survived ~15 minutes and at least two full tick
#: boundaries — long enough that ordinary scheduling jitter, a deploy restart or
#: a single slow provider read cannot produce it.
#:
#: The brief asks for alerts on *sustained* conditions, and the production
#: measurement is why: ``UNVERIFIED_DRAIN_BEHIND`` was 100% of the catalogue and
#: had been for days. A rule that paged on the instantaneous value would have
#: been firing continuously since the catalogue passed ~48 products, and an
#: alert that is always on is one nobody reads.
SUSTAINED_OBSERVATIONS = 3


def sustained(observations: Iterable[Mapping[str, Any]], predicate,
              *, required: int = SUSTAINED_OBSERVATIONS) -> bool:
    """Has ``predicate`` held for the last ``required`` observations?

    A pure function over a caller-supplied history — no module state, no clock,
    no timer. Reservation expiry learned this the hard way: anything that keeps
    its condition in process memory forgets on restart and cannot be reasoned
    about across two workers. The history belongs to whoever is storing
    snapshots; this only answers the question.

    Fewer than ``required`` observations is ``False``. A condition cannot be
    *sustained* across a window we have not observed, and the alternative —
    treating a short history as satisfying the rule — would fire on the first
    snapshot after every deploy.
    """
    recent = list(observations)[-max(1, int(required)):]
    if len(recent) < max(1, int(required)):
        return False
    return all(bool(predicate(item)) for item in recent)


def alert_conditions(observations: Sequence[Mapping[str, Any]],
                     *, required: int = SUSTAINED_OBSERVATIONS) -> dict:
    """Which sustained conditions are currently true, given a snapshot history.

    Deliberately does not include "any listing is ``UNVERIFIED_DRAIN_BEHIND``".
    That is production's steady state, not an incident, and the brief forbids
    tuning the capacity that would change it — so paging on it would hand the
    owner a permanent alarm describing a decision they already made.

    What is alertable is a condition that is *new information*:

    ``refusing`` — a buyer is being turned away right now. Zero in production
    today, so any non-zero value is a change.

    ``worker_stopped`` — nothing is reconciling at all. Distinct from behind:
    behind still produces confirmations, just too slowly.

    ``exposure_complete`` — *every* applicable listing is selling unverified.
    True in production today, which is precisely why it is reported as a
    standing condition rather than an incident; it is here so that the day it
    becomes false, or the day it becomes true on a deployment where it was not,
    somebody can see it.

    ``backlog_growing`` — the queue's front is further behind on every
    observation in the window. The one condition that distinguishes a bounded
    oversubscription from a runaway one, and the only one that cannot be read
    off a single snapshot.
    """
    def _refusing(snap):
        return sum(snap.get("states", {}).get(s, 0) for s in REFUSING_STATES) > 0

    def _stopped(snap):
        states = snap.get("states", {})
        return (states.get(UNVERIFIED_WORKER_STOPPED, 0)
                + states.get(UNVERIFIED_DRAIN_STALLED, 0)) > 0

    def _complete_exposure(snap):
        applicable = snap.get("applicable", 0)
        unverified = sum(snap.get("states", {}).get(s, 0) for s in UNVERIFIED_STATES)
        return bool(applicable) and unverified == applicable

    recent = list(observations)[-max(1, int(required)):]
    growing = False
    if len(recent) >= max(2, int(required)):
        lags = [float(s.get("queue_overdue_seconds") or 0.0) for s in recent]
        # Strictly increasing, not merely "higher than the first". A queue that
        # spiked once and has been draining ever since is recovering, and
        # calling that growth would alert on the good case.
        growing = all(b > a for a, b in zip(lags, lags[1:]))

    return {
        "refusing": sustained(observations, _refusing, required=required),
        "worker_stopped": sustained(observations, _stopped, required=required),
        "exposure_complete": sustained(observations, _complete_exposure, required=required),
        "backlog_growing": growing,
        "observations": len(list(observations)),
        "required": max(1, int(required)),
    }


# --------------------------------------------------------------------------
# The snapshot
# --------------------------------------------------------------------------

def snapshot(cur, *, listing_ids: Sequence[Any] | None = None,
             now: Any = None) -> dict:
    """Classify every purchasable listing and summarise. SELECTs only.

    ``listing_ids`` is injectable so a caller can scope this to one store, and
    so the tests can drive it without a catalogue. ``None`` means "every
    published listing that could actually be bought" — quantity and price above
    zero, because production carries 196 published listings of which only 44
    are purchasable, and a percentage over the wrong denominator is how a
    catalogue-wide problem gets reported as a quarter of one.

    The drain latch is read once and passed into every ``evaluate`` call, which
    is both cheaper and more correct than re-reading it per listing: a snapshot
    whose listings disagreed about whether the worker was running would not
    describe any moment that existed.
    """
    evidence = gate.reconciliation_evidence(cur, now=now)
    # Reaching past the underscore deliberately. The public alternative is
    # ``fulfillment.drain_status``, which opens its own ``db.connect()`` and runs
    # ``ensure_schema`` first — DDL, from a caller that may already hold a write
    # transaction, which is a known way to hang a route on PostgreSQL. The gate's
    # own docstring makes that argument and takes the same shortcut for the same
    # reason. A rename of this function reds the snapshot tests below rather than
    # silently reporting a backlog of zero.
    queue_overdue = gate._queue_overdue_by(cur, now=now)

    if listing_ids is None:
        cur.execute("SELECT id FROM marketplace_listings WHERE status='published' "
                    "AND COALESCE(quantity,0) > 0 AND COALESCE(price_minor,0) > 0 "
                    "ORDER BY id")
        listing_ids = [dict(row)["id"] for row in (cur.fetchall() or [])]

    states = {name: 0 for name in CANONICAL_STATES}
    ages: list[float] = []
    for listing_id in listing_ids:
        verdict = gate.evaluate(cur, listing_id=listing_id, evidence=evidence, now=now)
        states[classify(verdict)] += 1
        age = verdict.get("confirmation_age_seconds")
        if age is not None:
            ages.append(float(age))

    examined = len(list(listing_ids))
    applicable = examined - states[NOT_APPLICABLE]
    unverified = sum(states[s] for s in UNVERIFIED_STATES)
    refusing = sum(states[s] for s in REFUSING_STATES)

    def _rate(count):
        # None, not 0.0, when there is no denominator. A rate of zero reads as
        # "measured and healthy"; this is "nothing to measure".
        return round(count / applicable, 4) if applicable else None

    return {
        "latch_state": evidence.get("state"),
        "latch_running": bool(evidence.get("running")),
        "queue_overdue_seconds": round(float(queue_overdue), 1),
        "examined": examined,
        "applicable": applicable,
        "states": states,
        "unverified": unverified,
        "refusing": refusing,
        # The two headline rates, over the applicable denominator only.
        "drain_behind_rate": _rate(states[UNVERIFIED_DRAIN_BEHIND]),
        "unverified_rate": _rate(unverified),
        "refusal_rate": _rate(refusing),
        "confirmation_age": {
            "n": len(ages),
            "p50": percentile(ages, 50),
            "p95": percentile(ages, 95),
            "p99": percentile(ages, 99),
            "max": max(ages) if ages else None,
            "over_gate_max": sum(
                1 for a in ages if a > gate.CONFIRMATION_MAX_AGE_SECONDS),
            "gate_max_seconds": gate.CONFIRMATION_MAX_AGE_SECONDS,
        },
    }
