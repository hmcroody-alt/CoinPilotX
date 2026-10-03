"""Whether a drop-shipped listing may take a buyer's money right now. §22.

The gap this closes
-------------------
``§23``/``§24`` keep a listing's price and stock in step with its supplier, but
only as often as the reconciler runs — the inventory cadence is 900 seconds. A
buyer who checks out fourteen minutes after a supplier sold out is charged for
something nobody can ship, and the reconciliation that would have caught it
arrives after the money. So the last moment before a buyer is committed is its
own decision point, and this module is that decision.

Why it reads stored evidence instead of calling the supplier
------------------------------------------------------------
The obvious implementation is a provider read at checkout. It is the wrong one
here, for three separate reasons and any one of them would be enough:

* **All three checkout lanes hold an open database transaction at this point.**
  The cart is inside ``_with_db``, the offers lane inside its own handler, and
  ``bot.api_pulse_payments_checkout`` manages its own ``conn``/``cur`` — none of
  them have committed. A network call there holds a write transaction open for
  the duration of somebody else's outage.
* **CJ's quota is metered and finite.** Spending a call per checkout — per
  *attempt*, including the ones that fail for unrelated reasons — is the cheapest
  way to run out of reads for the reconciler that actually keeps the catalogue
  honest.
* **It makes provider latency the buyer's latency**, and provider downtime a
  total checkout outage, at the exact moment the buyer is most likely to leave.

So this function does no I/O of its own beyond the cursor it is handed. It reads
what the reconciler already wrote — ``marketplace_product_sources.sync_state``
and ``last_synced_at``, and the variants' own supplier stock states — and decides
whether that evidence is good enough to charge against. Refreshing the evidence
is the worker's job; judging it is this one's.

The honest problem with freshness in *this* deployment
------------------------------------------------------
This section used to say the reconciler was switched off — ``run_tick`` returning
``{"status": "disabled"}`` until ``CJ_RECONCILIATION_ENABLED`` is set,
``worker.run_once`` behind ``policy.require_network()``, both unset in production,
so every listing carried a NULL confirmation and the latch read
``NO_DRAIN_HAS_EVER_RUN``. **That is no longer true and has not been since
2026-09-17**, when ``supplier_worker`` was given a Railway service of its own with
both flags set. Measured 2026-10-01: the latch reads ``DRAINING``, and all 196
drop-shipped sources are ``SYNCED`` with a non-NULL ``last_synced_at``.

The premise going stale is what made the next defect invisible, so it is worth
naming: every reasoning step below that begins "since nothing is re-reading"
inverted the moment something was. The tier-2 strict path, which had never
executed in production, became the path that *every* drop-shipped checkout takes.

What that exposed is a third way for a reconciler to fail to refresh a
confirmation, and the latch cannot see it. ``worker.run_once`` is bounded to 20
reads a tick and ``supplier_worker`` ticks every 300s, so throughput is fixed at
20 jobs per 300s no matter how large the catalogue is, while demand is two
recurring jobs per product. A full sweep therefore costs ``2N / 20 * 300``
seconds: 660s at the 22 listings this module shipped against, and 5925s at the 196
it now serves. Past roughly 90 products the sweep is longer than
:data:`CONFIRMATION_MAX_AGE_SECONDS` and the strict path refuses a growing share
of the catalogue — not because anything is wrong with those listings, but because
the gate's clock is faster than the reconciler it is measuring. See
:data:`DRAIN_BEHIND`, which is the state that distinguishes the two, and §24: a
freshness guarantee the queue was never going to deliver is not a safety property.

A gate that demanded freshness anyway would take every drop-shipped listing off
sale the moment it shipped. That is not this gate catching a real problem; it is
a change in *what we check* wearing the costume of a change in *what is true*.
Nothing about those listings got worse when this file was added.

So the strictness follows the evidence that exists, in two tiers, and the tiers
are ordered by the *kind* of evidence rather than by how it arrived:

1. **An affirmative report of a problem refuses, unconditionally.** Every active
   variant out of stock (:data:`REASON_SOLD_OUT`), or a source state in
   :data:`FAILED_SYNC_STATES`. None of these expires and none of them depends on a
   reconciler running — a supplier that said "none left" has not become less sold
   out by nobody asking again since, and a revoked credential does not start
   working because the worker stopped.
2. **An absence of recent reassurance is judged against what was on offer.** If
   nothing is refreshing confirmations, or this listing has never had one,
   freshness cannot be demanded and the sale is allowed *and marked*: the decision
   carries ``unverified`` plus the latch state and which kind of absence it was, so
   the lane records it on the transaction and the gap is auditable instead of
   absorbed. If a reconciler is running and this listing *does* have a
   confirmation, that confirmation has to be current — older than
   :data:`CONFIRMATION_MAX_AGE_SECONDS`, dated in the future, or unreadable all
   refuse, because now silence means something broke.

Why "never confirmed" is not "went stale"
-----------------------------------------
The first draft keyed strictness on one fact — is a reconciler running — and then
demanded a per-listing confirmation. Those two decouple immediately.
``worker.run_once`` writes ``record_drain_tick(completed=True)`` as its last
statement, and reaches it even on a tick that claimed zero jobs, so the latch
reads ``DRAINING`` within seconds of the first tick. Confirmations arrive far more
slowly: ``revisions`` writes ``last_synced_at`` one listing at a time, twenty jobs
a tick, on the 3600-second product cadence. So for hours after the worker starts,
every listing is simultaneously "a reconciler is up" and "this one has never been
confirmed".

Under the first draft that combination refused, and the measured consequence on
this deployment was total: all 22 drop-shipped sources carry
``last_synced_at IS NULL`` (``link_source`` never writes it, and ``importer`` and
``gateway`` both go through ``link_source``), so enabling the worker would have
refused 100% of drop-shipped checkouts and kept refusing until coverage caught
up — while telling each buyer to "try again shortly". All 22 also read
``sync_state='SYNCED'``, so nothing in the data looked wrong.

The conflation was in one comparison: an ``age`` of ``None`` meant both "no stamp"
and "a stamp nobody can parse". Those are opposite facts. A listing with no
confirmation has no evidence that could have gone stale, and absence of evidence
is not evidence of a sell-out; a listing with an unreadable confirmation has a
failed write or a schema drift, and "I cannot tell when this was confirmed" reads
safely as "it was not". So they now take different branches, and the strict path
applies to the listings that genuinely have something to be stale.

The cost is explicit: a never-confirmed listing is sellable on the annotation
rather than refused, which narrows what this gate blocks. That is the intended
trade. §22's job is to refuse a sale we have positive reason to believe cannot be
filled — an empty column is not that reason, and a gate whose first act on being
switched on is to close the store has stopped being a safety feature.

What is *not* traded away is tier 1. Widening the absent-evidence case is only
defensible while affirmative bad evidence still refuses through it, which is why
:data:`FAILED_SYNC_STATES` moved above the unverified allow rather than staying
below it: otherwise "we have no confirmation" would have quietly outranked "the
last read told us the product is gone".

The effect is that deploying the worker makes this gate strict as confirmations
arrive, listing by listing, rather than all at once on a latch, and until a
listing is covered it says out loud that it cannot vouch for it.

Two facts this gate did not check, and the money it cost
-------------------------------------------------------
Everything above is about *availability*: can the supplier still ship this. Two
other things can be known-false before a buyer is charged, and neither was asked.

**Nothing bound.** A drop-shipped listing sells exactly the variant named by
``marketplace_product_sources.provider_variant_id``; ``fulfillment.create_intent``
resolves every line through ``gateway.get_product_binding``, which raises
``product_binding_required`` (409) when that column is NULL and, failing that,
``create_intent`` raises ``product_binding_mismatch`` (400) when the line's
``vid`` is not the bound one. Both run *after* the charge, so a live, priced,
in-stock, unbound listing took the buyer's money and only then failed.
Production listing 35 is exactly that listing — publicly purchasable, with no
binding — and it is the reason :data:`REASON_UNBOUND` exists. Checking it here
moves a refusal that already existed to the side of the payment where it is still
free, which is the same argument the module is built on.

(``shop_binding_required``, raised a few lines earlier in ``create_intent``, is a
different and coarser fact: the *connection* has no CJ shop bound, which stops
every order on it rather than one listing. It is not what listing 35 hits.)

**Known loss.** Nothing on any checkout path compared a sale price to a supplier
cost. Measured in production 2026-10-01: 477 of 3797 variants are priced below
their landed cost, across 20 listings that the supplier reconciler had *already*
flagged ``SELLING_BELOW_COST`` in ``attention_json`` — so the system knew, wrote
it down, and sold anyway. :data:`REASON_NEGATIVE_MARGIN` closes that, and the
check is deliberately narrow: it refuses a loss, not a disappointing margin. The
store's 68% target is a commercial preference, and a gate that enforced a
preference would have taken profitable listings off sale.

Both are tier 1 by the same test the tier was built on. They are affirmative
statements that something is wrong rather than an absence of reassurance; neither
expires; neither depends on a reconciler having run. And neither is keyed to a
listing id — they are properties re-derived per checkout from the listing's own
binding and the supplier's own stored cost, so a listing that is fixed starts
selling again with nothing to un-deploy, and the next listing to break is caught
without anybody noticing it broke.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from services import marketplace_supplier_schema as supplier_schema
from services import marketplace_variants as variants
from services.business_os.suppliers import pricing, store_policy
from services.business_os.suppliers.fulfillment import DRAIN_STALL_SECONDS

__all__ = [
    "CONFIRMATION_KNOWN",
    "CONFIRMATION_MAX_AGE_SECONDS",
    "CONFIRMATION_NEVER",
    "CONFIRMATION_STATES",
    "CONFIRMATION_UNREADABLE",
    "DECISION_ALLOW",
    "DECISION_REFUSE",
    "DRAIN_BEHIND",
    "NOT_APPLICABLE",
    "REASON_NEGATIVE_MARGIN",
    "REASON_SOLD_OUT",
    "REASON_STALE_CONFIRMATION",
    "REASON_UNBOUND",
    "REFUSAL_CODES",
    "WIRE_CODES",
    "reconciliation_evidence",
    "evaluate",
    "screen",
    "refusal_code",
    "audit",
    "audit_for",
]

#: Three inventory cadences. The reconciler re-reads inventory every 900s
#: (``business_os.suppliers.worker.CADENCE["inventory"]``), so this tolerates two
#: missed ticks before it stops trusting the answer. One cadence would refuse on
#: ordinary scheduling jitter; ten would make the window this gate exists to close
#: wider than the one it started with. The derivation is pinned by a test rather
#: than left as a comment, because a cadence change that silently widened this
#: would be invisible here.
CONFIRMATION_MAX_AGE_SECONDS = 2700

#: How far in the future a confirmation may sit before it stops being evidence.
#: A stamp ahead of now is not a recent read — it is a clock disagreement or a bad
#: write, and without this an ``age`` of −6 hours passes a ``> MAX`` comparison and
#: reads as permanently fresh. Small and non-zero because the writer
#: (``revisions``) and this reader are not guaranteed to be the same process, and
#: refusing checkouts over a second of NTP drift would be its own outage.
CLOCK_SKEW_TOLERANCE_SECONDS = 300

#: What a listing's ``last_synced_at`` column is, as three distinguishable facts
#: rather than a timestamp-or-None.
#:
#: ``NEVER`` and ``UNREADABLE`` both produce no age, and collapsing them is the
#: defect the module docstring describes: one is a listing the reconciler has not
#: reached yet, the other is a write that went wrong. The first is the normal state
#: of every listing on this deployment; the second should never happen and means
#: something is broken. Treating them alike refuses the whole catalogue to guard
#: against a corruption nobody has observed.
#:
#: Named and exported because the value travels out on every decision and into the
#: audit annotation, so a post-mortem can separate "sold without a confirmation
#: because none had been written yet" from anything else.
CONFIRMATION_NEVER = "NEVER"
CONFIRMATION_UNREADABLE = "UNREADABLE"
CONFIRMATION_KNOWN = "KNOWN"
CONFIRMATION_STATES = (CONFIRMATION_NEVER, CONFIRMATION_UNREADABLE, CONFIRMATION_KNOWN)

DECISION_ALLOW = "ALLOW"
DECISION_REFUSE = "REFUSE"

#: Returned when the listing has no supplier at all, or has one the merchant
#: fulfils themselves. Distinct from ``ALLOW`` on purpose: "this gate has nothing
#: to say" and "this gate looked and was satisfied" are different facts, and a
#: caller that conflated them could not tell a hand-authored listing from a
#: drop-shipped one that passed.
NOT_APPLICABLE = "NOT_APPLICABLE"

REASON_SOLD_OUT = "SUPPLIER_SOLD_OUT"
REASON_STALE_CONFIRMATION = "SUPPLIER_UNCONFIRMED"

#: No supplier variant is bound to this listing, so no order could name a
#: physical item even if the buyer paid.
#:
#: This is not a new rule, it is an existing rule moved to the only side of the
#: payment where it can still help. ``fulfillment.create_intent`` already refuses
#: an unbound line — ``gateway.get_product_binding`` raises
#: ``product_binding_required`` (409) on a NULL ``provider_variant_id``, and a
#: line naming any other variant then fails ``product_binding_mismatch`` (400)
#: — but both run *after*
#: the charge. So the pre-change behaviour of a live, priced, in-stock, unbound
#: drop-shipped listing was: take the money, then discover nobody can ship it.
#: Production listing 35 is that listing, and it is why this reason exists.
#:
#: Tier 1, and the first check of the tier. An absent binding does not expire, is
#: not a fact about a reconciler, and cannot be fixed by asking the supplier
#: again — only the seller can fix it by choosing a variant. It is also checked
#: *above* the sold-out comparison on purpose: that comparison reads every active
#: variant, and ``drafts._sold_variant`` is explicit that an unbound listing's
#: other rows are "catalogue -- what the supplier offers -- not stock this listing
#: can sell". Asking whether catalogue rows are in stock cannot tell you anything
#: about a listing that has not chosen one of them.
REASON_UNBOUND = "SUPPLIER_VARIANT_UNBOUND"

#: The money is known to run the wrong way: the amount this checkout would
#: charge is below what the supplier currently costs to fulfil it.
#:
#: Tier 1 for the same reason the others are. A cost that exceeds the price is an
#: affirmative, already-recorded fact rather than an absence of reassurance, it
#: does not become false because no reconciler has re-read it, and no freshness
#: window applies: the most recent thing the supplier told us is that this sale
#: loses money.
#:
#: Deliberately not a margin *target*. This refuses only a loss — ``price <
#: cost`` — and has nothing to say about a sale that is merely thinner than the
#: seller would like. The store's 68% target is a commercial preference and
#: enforcing it here would take roughly a hundred profitable listings off sale to
#: satisfy a number nobody asked checkout to defend. See ``catalog_readiness``'s
#: ``below_target``, which reports that distinction instead of acting on it.
REASON_NEGATIVE_MARGIN = "SUPPLIER_NEGATIVE_MARGIN"

#: The only codes a lane may return from this gate. Enumerated so the client
#: strings and the tests are written against one list.
REFUSAL_CODES = (REASON_SOLD_OUT, REASON_STALE_CONFIRMATION, REASON_UNBOUND,
                 REASON_NEGATIVE_MARGIN)

#: The code that goes on the wire, which is not the same as the reason recorded
#: internally. ``marketplace_cart_routes._error`` documents a fixed vocabulary
#: shared with ``mobile-native/src/api/marketplaceErrors.ts``, and a code outside it
#: falls through to the server's own prose.
#:
#: A supplier sell-out is, to the buyer, the same fact as any other sell-out, and
#: the client already has copy for ``OUT_OF_STOCK`` — inventing a second code for
#: one fact would leave the buyer's screen depending on which subsystem noticed.
#:
#: ``SUPPLIER_UNCONFIRMED`` deliberately has no mapping. The nearest existing code
#: is ``ITEM_UNAVAILABLE``, whose copy ("no longer available") describes something
#: permanent, and this state is transient by definition — the buyer's next move is
#: to try again shortly, which that copy forecloses. So it travels as itself and
#: relies on ``buyerErrorCopy``'s documented fallback to server prose for a handled
#: 4xx. That is why :data:`MESSAGES` has to be buyer-complete on its own, including
#: saying that no charge was made.
#:
#: :data:`REASON_UNBOUND` and :data:`REASON_NEGATIVE_MARGIN` are unmapped for the
#: mirror-image reason. ``OUT_OF_STOCK`` would be a lie — the supplier has plenty
#: — and ``ITEM_UNAVAILABLE``'s "no longer available" is wrong in a subtler way:
#: it says the item used to be purchasable and has stopped, whereas an unbound
#: listing was never fulfillable and its own seller has not finished setting it
#: up. Rather than pick the least-wrong existing string, both travel as themselves
#: on the same documented server-prose fallback.
WIRE_CODES = {REASON_SOLD_OUT: "OUT_OF_STOCK"}

#: What the buyer reads. Neither names the supplier, the provider or the
#: connection — §27 applies to a refusal as much as to a success, and "our
#: supplier CJ is down" tells a buyer something about the merchant's business
#: that the merchant did not choose to publish.
#:
#: :data:`REASON_UNBOUND` and :data:`REASON_NEGATIVE_MARGIN` share one sentence,
#: and that is not laziness. To the buyer they are the same event — the seller has
#: this listed but cannot sell it to them right now — and the buyer's next move is
#: identical in both. Writing two sentences would differentiate them for the only
#: audience that cannot act on the difference, while telling that audience which
#: of the two it was: one of them is "this seller priced below their own cost",
#: which is a fact about the merchant's margins that the merchant did not choose
#: to publish. The two stay distinguishable where it matters — ``reason`` on the
#: decision, and therefore the ops signal and the audit trail.
#:
#: Neither says "try again shortly". That is true of a stale confirmation and
#: false of these two: nothing a buyer does clears them, only the seller binding a
#: variant or fixing a price does, so forecasting a retry would be a promise this
#: gate cannot keep.
MESSAGES = {
    REASON_SOLD_OUT: "This item just went out of stock. You have not been charged.",
    REASON_STALE_CONFIRMATION: (
        "We can't confirm this item is still available right now. "
        "You have not been charged — please try again shortly."),
    REASON_UNBOUND: (
        "This item isn't available to buy right now. You have not been charged."),
    REASON_NEGATIVE_MARGIN: (
        "This item isn't available to buy right now. You have not been charged."),
}

#: Latch states from ``business_os.suppliers.fulfillment.drain_status`` that mean
#: the reconciler is genuinely producing fresh confirmations. ``DRAIN_STALLED``,
#: ``TICKING_BUT_NOT_COMPLETING`` and ``DRAIN_BEHIND`` are deliberately absent: a
#: worker that is deployed but broken, or deployed but outrun by its own queue,
#: has stopped refreshing anything *on the schedule this gate assumes*, so
#: demanding freshness of it would refuse every checkout for as long as the
#: condition lasted. Those states fall back to the unverified path, which is the
#: same answer as "not deployed" because it is the same situation — nothing is
#: re-reading this listing inside the window.
RUNNING_STATES = ("DRAINING",)

#: The reconciler is alive and completing ticks, but its own queue is further
#: behind than the freshness this gate demands — so a stale confirmation is the
#: queue's normal state rather than a fact about the listing.
#:
#: This is the third member of the same family as ``DRAIN_STALLED``, and it was
#: missing for the same reason that one was: the latch answers "is a worker
#: running", and the gate was reading that as "is a worker keeping up". Those
#: decouple as soon as the catalogue outgrows the worker's throughput.
#: ``worker.run_once`` is bounded to 20 reads a tick and ``supplier_worker`` ticks
#: every 300s, so a catalogue of N products carries 2N recurring jobs and a full
#: sweep costs ``2N / 20 * 300`` seconds. At the 22 listings this gate shipped
#: against that is 660s and every cadence is met. At 196 it is 5925s, every
#: confirmation spends most of its life older than
#: :data:`CONFIRMATION_MAX_AGE_SECONDS`, and the strict branch below refuses
#: roughly half the sellable catalogue at any instant — while telling each buyer
#: to "try again shortly", which is the one thing that cannot help.
#:
#: Measured in production 2026-10-01: 196 dropship sources, all ``SYNCED``, all
#: 3797 variants ``IN_STOCK``, zero job failures, snapshots 45s old — and yet
#: 18 of the 37 purchasable listings refused, worst confirmation age 6146s
#: against a worst inventory revisit lag of 6145s. The two numbers being the same
#: is the proof: the staleness was the queue's, not the supplier's. Re-measured
#: an hour later the refusals were 22 of 37, which is the other half of the
#: proof — the count tracks how far the queue has drifted, so it grows on its own
#: and no supplier event has to happen for a buyer to start seeing this.
DRAIN_BEHIND = "DRAIN_BEHIND"

#: Source states that are an affirmative report of a failure, as opposed to an
#: absence of a recent success. ``STALE`` means the last read did not apply,
#: ``ERROR`` that it threw, ``DISCONNECTED`` that the merchant's credential no
#: longer works, ``REMOVED`` that the provider dropped the product. Each is a
#: reason to refuse in its own right and none of them expires, so they are checked
#: before anything to do with the reconciler's latch or the confirmation clock.
#:
#: ``PENDING`` is deliberately absent, and it is the trap in this tuple: it is the
#: column DEFAULT, so every source has it before its first read. Including it would
#: refuse every freshly imported listing — the same catalogue-wide refusal this
#: module's docstring is about, arriving by a different route.
FAILED_SYNC_STATES = (supplier_schema.SYNC_STALE, supplier_schema.SYNC_ERROR,
                      supplier_schema.SYNC_DISCONNECTED, supplier_schema.SYNC_REMOVED)


def _parse(stamp: Any) -> datetime | None:
    """Read one of the two timestamp spellings this schema uses, or give up.

    ``marketplace_product_sources`` is written without an offset suffix by both
    of its writers (``variants._now`` and ``revisions._row_time``), but a row
    carrying ``+00:00`` or a trailing ``Z`` from some earlier writer must not
    crash a checkout — an unparseable stamp is simply not evidence of freshness,
    which is the same answer as no stamp at all.
    """
    text = str(stamp or "").strip()
    if not text:
        return None
    if text.endswith(("z", "Z")):
        text = text[:-1]
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed.replace(tzinfo=None) if parsed.tzinfo else parsed


def _confirmation(stamp: Any, now: datetime) -> tuple[str, float | None]:
    """Which of the three things ``last_synced_at`` can be, and its age if it has one.

    Three outcomes rather than an age-or-None, because the caller has to tell
    :data:`CONFIRMATION_NEVER` from :data:`CONFIRMATION_UNREADABLE` and an age of
    ``None`` cannot carry that distinction — which is precisely the bug the module
    docstring describes.

    One function, one parse. The earlier shape asked ``_parse`` whether the stamp
    was readable and ``_age_seconds`` how old it was, and two independent reads of
    the same column can disagree after an edit to one of them. Here the invariant
    is structural: the age is a float exactly when the state is
    :data:`CONFIRMATION_KNOWN`, and ``None`` otherwise. ``evaluate`` compares the
    age numerically without a ``None`` guard *because* of that invariant, so it is
    pinned by its own test rather than defended by a redundant check.
    """
    text = str(stamp or "").strip()
    if not text:
        return CONFIRMATION_NEVER, None
    parsed = _parse(text)
    if parsed is None:
        return CONFIRMATION_UNREADABLE, None
    return CONFIRMATION_KNOWN, (now - parsed).total_seconds()


def reconciliation_evidence(cur, *, now: Any = None) -> dict:
    """Is anything refreshing supplier confirmations? Read, never assumed.

    Deliberately a plain ``SELECT`` on the caller's own cursor rather than a call
    to ``fulfillment.drain_status``. That function is the right authority on what
    the latch *means* and this mirrors its rule, but it opens its own connection
    and calls ``ensure_schema`` first — DDL, from inside a request that is already
    holding a write transaction open. That is a known way to hang a route on
    PostgreSQL, and a checkout is the worst place to discover it.

    The stall window is imported from ``fulfillment`` rather than restated, so the
    two readers of this latch cannot drift into disagreeing about when a worker
    has stopped. Both timestamps are epoch seconds (``DOUBLE PRECISION``), not the
    ISO strings the rest of this module parses — hence the separate clock.

    A missing table is not an error. ``business_os_supplier_drain_ticks`` only
    exists where the supplier subsystem has been initialised, and a deployment
    without it has certainly never drained.
    """
    try:
        cur.execute("SELECT started_at, completed_at FROM "
                    "business_os_supplier_drain_ticks WHERE scope='worker'")
        row = cur.fetchone()
    except Exception:
        return {"state": "NO_DRAIN_HAS_EVER_RUN", "running": False, "completed_at": None}
    row = dict(row) if row else {}
    started, completed = row.get("started_at"), row.get("completed_at")
    if not started:
        state = "NO_DRAIN_HAS_EVER_RUN"
    elif not completed:
        state = "TICKING_BUT_NOT_COMPLETING"
    elif _epoch(now) - float(completed) > DRAIN_STALL_SECONDS:
        # The state this function existed without for one draft, and the omission
        # inverted the whole design. A worker that ran once and died leaves
        # `completed_at` set forever; without this branch that reads as DRAINING,
        # freshness gets demanded of a reconciler that stopped, and every
        # drop-shipped checkout refuses until somebody notices. A stalled worker
        # is not refreshing anything, which is the same situation as one that was
        # never deployed, so it takes the same unverified path.
        state = "DRAIN_STALLED"
    elif _queue_overdue_by(cur, now=now) > CONFIRMATION_MAX_AGE_SECONDS:
        # A worker that is ticking, completing, and still losing. The latch above
        # can only say a tick finished; it cannot say the queue is being kept
        # inside the window the caller is about to demand. See `DRAIN_BEHIND`.
        state = DRAIN_BEHIND
    else:
        state = "DRAINING"
    return {"state": state, "running": state in RUNNING_STATES,
            "completed_at": completed}


def _queue_overdue_by(cur, *, now: Any = None) -> float:
    """How long the reconciler's oldest unclaimed job has been due, in seconds.

    The one measurement that separates "a worker is running" from "a worker is
    keeping up". ``available_at`` is when a job next became eligible, so the
    minimum across the table is the front of the queue, and ``now`` minus that is
    how far behind the reconciler currently is. Covered by
    ``idx_supplier_sync_due``, so this is an index read rather than a scan.

    Returns ``0.0`` rather than raising or returning None, for three cases that
    all mean the same thing — *nothing here proves the reconciler is behind*:
    the table does not exist (a deployment without the supplier subsystem), it is
    empty (no work is queued, so none is late), or the front of the queue is still
    in the future. Zero is the value that leaves the caller's comparison falling
    through to ``DRAINING``, which keeps this gate exactly as strict as it was
    before this function existed.

    That asymmetry is deliberate and it is the safety property of this change:
    leniency is granted only on a positive, measured backlog, never on an absence
    of evidence about one. It is the same rule tier 1 applies in the other
    direction — the decision follows what was actually observed, so a read that
    fails cannot quietly widen what this gate allows.
    """
    try:
        cur.execute("SELECT MIN(available_at) AS oldest FROM "
                    "business_os_supplier_sync_jobs")
        row = cur.fetchone()
    except Exception:
        return 0.0
    oldest = (dict(row) if row else {}).get("oldest")
    if oldest is None:
        return 0.0
    try:
        return max(0.0, _epoch(now) - float(oldest))
    except (TypeError, ValueError):
        return 0.0


def _orderable(rows: Sequence[Mapping[str, Any]]) -> list[dict]:
    return [dict(row) for row in rows
            if str(row.get("status") or "active").strip().lower() == "active"]


def _bound_variant(rows: Sequence[Mapping[str, Any]],
                   source: Mapping[str, Any]) -> dict | None:
    """The one variant this listing can actually ship, or None if there isn't one.

    Mirrors :func:`services.business_os.suppliers.drafts._sold_variant`, which is
    the authority on what a binding is and why only one variant counts:
    ``marketplace_product_sources.provider_variant_id`` names it, and
    ``fulfillment.create_intent`` will accept no other. The agreement between the
    two implementations is pinned by a test rather than left to inspection,
    because a predicate that is copied and not compared is a predicate that
    drifts.

    It is reimplemented here rather than imported for one reason: ``drafts``
    pulls in the supplier feature-flag surface and the whole draft/publish
    pipeline, and a checkout should not be able to fail because an import three
    modules deep raised. The two callers want different inputs anyway — ``drafts``
    passes variants that have already been through ``pricing.quote``, and this
    wants the raw rows, which is the same reason ``listing_readiness`` takes raw
    rows too.

    Returns ``None`` in both of the cases ``_sold_variant`` does: nothing bound,
    and a bound id naming a variant this listing no longer has. The second is
    drift rather than absence, and the consequence is identical — there is no
    variant anybody can prove will ship — so it is reported as the same problem.
    """
    bound = str((source or {}).get("provider_variant_id") or "").strip()
    if not bound:
        return None
    for row in rows:
        if str(row.get("provider_variant_id") or "").strip() == bound:
            return dict(row)
    return None


def _shipping_allowance(cur, source: Mapping[str, Any]) -> int | None:
    """This store's declared per-unit freight, by plain read. ``None`` if undeclared.

    A ``SELECT`` on the caller's cursor rather than
    ``store_policy.resolve_shipping_allowance``, for exactly the reason
    :func:`reconciliation_evidence` does not call ``fulfillment.drain_status``:
    that function routes through ``get_policy``, which calls ``ensure_schema``
    first. That is DDL, issued from inside a request already holding a write
    transaction open, which is a known way to hang a route on PostgreSQL — and a
    checkout is the worst place in the product to discover it.

    The value goes through ``pricing.normalize_shipping`` rather than ``int()`` so
    that this reader and ``get_policy`` cannot disagree about what a stored
    allowance means, and the table name is imported rather than spelled so they
    cannot disagree about where it lives.

    ``None`` on a missing table, a missing row or an unreadable value. All three
    mean the store has not declared freight, which is not the same as declaring it
    free — see ``store_policy``'s "No platform default" note. The caller must fall
    back to the item basis explicitly, and :func:`pricing.basis` is what does it.
    """
    try:
        cur.execute(
            f"SELECT shipping_allowance_cents FROM {store_policy.TABLE} "
            "WHERE business_id=? AND store_id=? LIMIT 1",
            (str(source.get("business_id") or ""), str(source.get("store_id") or "")))
        row = cur.fetchone()
    except Exception:
        return None
    if row is None:
        return None
    return pricing.normalize_shipping(dict(row).get("shipping_allowance_cents"))


def _economics(cur, source: Mapping[str, Any], bound: Mapping[str, Any],
               price_minor: Any) -> tuple[int | None, int | None]:
    """What this sale charges per unit, and what the unit costs. ``None`` for unknown.

    **Which price.** ``price_minor`` when the lane supplied it, the bound variant's
    stored ``price_cents`` otherwise. The lane's figure wins because it is the
    money: the cart charges ``price_snapshot_minor``, captured when the line was
    added, so a cost that rose afterwards is invisible to the catalogue price and
    visible only here. That is §24's stale-cart case and it is the whole reason
    this takes a parameter instead of reading one number.

    Deliberately *not* the lower of the two. A snapshot above the current retail
    is not a loss on this sale even if the catalogue price is underwater, and
    refusing it would block a buyer over somebody else's future purchase. The
    catalogue being underwater is a real problem and it has its own reporting —
    ``catalog_readiness``'s ``below_landed_cost`` — rather than a refusal here.

    **Which cost.** :func:`pricing.basis`, which is landed cost where freight was
    declared and item cost where it was not. Calling it rather than adding the two
    numbers keeps §4's formula in one place; and its fallback is the honest one,
    because a store that has not declared freight has not declared it to be zero.
    The consequence is that an undeclared allowance makes this gate *more*
    permissive, never less: the bar drops to the item cost, and a sale below even
    that is still unambiguously a loss. Leniency on absent evidence, strictness on
    present evidence, which is the same asymmetry :func:`_queue_overdue_by`
    documents.
    """
    stored = bound.get("price_cents")
    charged = price_minor if price_minor is not None else stored
    try:
        charged_minor = int(charged) if charged is not None else None
    except (TypeError, ValueError):
        charged_minor = None
    _, basis_cost = pricing.basis(bound.get("cost_cents"),
                                  _shipping_allowance(cur, source))
    return charged_minor, basis_cost


def evaluate(cur, *, listing_id: Any, evidence: Mapping[str, Any] | None = None,
             price_minor: Any = None, now: Any = None) -> dict:
    """May this listing be charged for right now?

    Returns ``{"decision", "reason", "message", "code", "unverified", ...}``.
    ``decision`` is one of :data:`DECISION_ALLOW`, :data:`DECISION_REFUSE` or
    :data:`NOT_APPLICABLE`; every other key is present on every answer so a
    caller never has to guess whether a field exists.

    No quantity argument. A listing's *unit count* is already checked by each
    lane against ``marketplace_listings.quantity`` and by the reservation's own
    compare-and-swap, and re-deciding it here would be a second authority on the
    same question. What is asked here is different and narrower: does the
    supplier's own reported state still permit a sale at all.

    Which variant is likewise not asked of the *caller*, and no longer has to be:
    the listing's own binding answers it. This paragraph used to say a sell-out
    had to mean *every* active variant was out of stock, because no lane knows
    which variant a buyer wanted and refusing on one row would be a guess. The
    premise was right and the scope was wrong. It holds for a listing with nothing
    bound — which is now refused outright, see :data:`REASON_UNBOUND` — and it
    collapses the moment a binding exists, because the binding *is* the answer to
    "which variant", recorded by the seller and enforced by
    ``fulfillment.create_intent``. So the stock and cost questions below are asked
    of the one row an order can actually name, and the all-variants reading is gone
    rather than retained next to it: it had become a way for a sold-out bound
    variant to pass on the strength of a sibling nobody can buy.

    ``price_minor`` is the per-unit amount this checkout will charge, when the lane
    knows it. See :func:`_economics`.
    """
    now_dt = _clock(now)
    try:
        resolved = variants.coerce_listing_id(listing_id)
    except variants.VariantRejected:
        # `coerce_listing_id` raises rather than returning None, and an exception
        # escaping here would turn a checkout into a 500. It cannot happen from a
        # lane — all three resolved the listing from `marketplace_listings` before
        # they got this far — so this is a programmer-error path, and a reference
        # that names no listing has no supplier row by definition. Same answer as
        # a listing PulseSoc authored itself, which is the answer that leaves the
        # lane exactly as it behaved before this gate existed.
        return _not_applicable("no_supplier")
    source = variants.source_for(cur, resolved)
    if source is None:
        return _not_applicable("no_supplier")
    mode = str(source.get("fulfillment_mode") or "").strip().upper()
    if mode != supplier_schema.MODE_DROPSHIP:
        # The merchant holds this stock, counts it and ships it. A supplier's
        # opinion about someone else's warehouse is not a reason to refuse, which
        # is the same rule `revisions.apply_supplier_read` applies when it skips
        # STOCKED sources rather than writing over the merchant's count.
        return _not_applicable("merchant_stocked")

    running = bool((evidence or {}).get("running"))
    confirmation, age = _confirmation(source.get("last_synced_at"), now_dt)
    sync_state = str(source.get("sync_state") or "").strip().upper()
    #: Every answer below carries the same four facts, gathered once. A refusal
    #: that reported less than an allow would make the audit trail thinnest for
    #: exactly the decisions someone will come back to ask about.
    seen = {"evidence_state": _state_of(evidence), "confirmation": confirmation,
            "confirmation_age_seconds": age, "sync_state": sync_state}

    rows = _orderable(variants.variants_for(cur, int(source["listing_id"])))
    bound = _bound_variant(rows, source)
    seen["bound"] = bound is not None

    if bound is None:
        # Nothing to ship, and nothing a buyer or a supplier can do about it. The
        # first tier-1 check because the ones below it all reason about a specific
        # variant's stock or cost, and there is no specific variant yet; see
        # :data:`REASON_UNBOUND`.
        return _refuse(REASON_UNBOUND, **seen)

    # The bound variant's stock, not the catalogue's. A multi-variant listing
    # whose bound variant is sold out while a *different* variant is in stock used
    # to pass here — `states` was computed across every active row and `all()` is
    # False as soon as one row disagrees — and then `create_intent` demanded the
    # bound variant and the supplier could not fill it. The original all-variants
    # reading was correct for its own premise, stated in this function's docstring:
    # no lane knows which variant, so refusing would be a guess about which one the
    # buyer wanted. Once a binding exists there is no guess left to make, because
    # the binding *is* the answer to "which variant", so the question narrows to
    # the one row an order can actually name.
    if str(bound.get("stock_state") or "").strip().upper() == supplier_schema.STOCK_OUT_OF_STOCK:
        # Positively bad, and true regardless of freshness. A supplier that said
        # "none left" has not become less sold out by nobody asking again since.
        return _refuse(REASON_SOLD_OUT, **seen)

    if sync_state in FAILED_SYNC_STATES:
        # A read that was *attempted* and failed. The timestamp only ever proves
        # when a read last succeeded; this proves the most recent one did not —
        # the state a listing sits in while its supplier is unreachable, or after
        # the merchant revoked the connection, or after the provider dropped the
        # product.
        #
        # Above the latch and the confirmation checks, in the same tier as a
        # sold-out variant, because it is the same *kind* of fact: an affirmative
        # statement that something is wrong, not an absence of reassurance. It does
        # not become less true because nothing is re-reading — if anything a
        # stopped reconciler means it will stay true. An earlier arrangement had
        # this below the unverified allow, which made the decision depend on
        # whether the failed state happened to arrive with a timestamp: it does
        # today, from the one writer that sets both in a single UPDATE, and a
        # future writer that set only the state would have been silently allowed
        # through. Ordering it by the kind of evidence removes that dependency
        # rather than documenting it.
        return _refuse(REASON_STALE_CONFIRMATION, **seen)

    charged, basis_cost = _economics(cur, source, bound, price_minor)
    seen["charged_minor"] = charged
    seen["basis_cost_minor"] = basis_cost
    if charged is not None and basis_cost is not None and charged < basis_cost:
        # The last tier-1 refusal, and the only one that is about money rather
        # than about stock. Both sides have to be known: an unreadable cost is not
        # evidence of a loss, and refusing on one would take every listing with a
        # missing cost off sale — the catalogue-wide refusal this module's
        # docstring exists to argue against, arriving by a third route.
        return _refuse(REASON_NEGATIVE_MARGIN, **seen)

    if not running or confirmation == CONFIRMATION_NEVER:
        # Two different situations, one correct answer, and the reason is the same
        # in both: there is no evidence here that could have gone stale.
        #
        # `not running` — freshness cannot be demanded of a reconciler that has
        # never run or has stopped. `CONFIRMATION_NEVER` — freshness cannot be
        # demanded of a listing the running reconciler has not reached yet, which
        # is every listing for hours after the worker starts, because the latch
        # flips on tick #1 and confirmations arrive twenty jobs at a time.
        #
        # Allowed, and said out loud: the annotation carries which of the two it
        # was, so "we sold this without a confirmation" is a queryable fact rather
        # than a shrug.
        return _allow(unverified=True, **seen)

    if confirmation == CONFIRMATION_UNREADABLE:
        # Corrupt is not absent. Something wrote this column and nothing can read
        # it, which is a failed write or a format drift between `revisions` and
        # `_parse` — and the safe reading of "I cannot tell when this was
        # confirmed" is that it was not. Distinct branch from the age comparison
        # below because that comparison cannot run on a None.
        return _refuse(REASON_STALE_CONFIRMATION, **seen)

    if age > CONFIRMATION_MAX_AGE_SECONDS or age < -CLOCK_SKEW_TOLERANCE_SECONDS:
        # No `age is None` guard, and deliberately not: `_confirmation` returns a
        # float exactly when it returns KNOWN, and both other states returned
        # above. A guard here would be dead code that reads as load-bearing. The
        # invariant is pinned by a test instead.
        return _refuse(REASON_STALE_CONFIRMATION, **seen)

    return _allow(unverified=False, **seen)


def _clock(now: Any) -> datetime:
    if isinstance(now, datetime):
        return now.replace(tzinfo=None) if now.tzinfo else now
    if isinstance(now, (int, float)):
        return datetime.fromtimestamp(float(now), timezone.utc).replace(tzinfo=None)
    parsed = _parse(now)
    return parsed if parsed is not None else datetime.now(timezone.utc).replace(tzinfo=None)


def _epoch(now: Any) -> float:
    """The same instant as :func:`_clock`, in the drain latch's units.

    The latch stores ``time.time()`` floats while every other timestamp this
    module touches is an ISO string, so one of the two has to convert. Naive
    values are treated as UTC, matching what ``variants._now`` writes.
    """
    if isinstance(now, (int, float)) and not isinstance(now, bool):
        return float(now)
    moment = _clock(now)
    return moment.replace(tzinfo=timezone.utc).timestamp()


def _state_of(evidence: Mapping[str, Any] | None) -> str:
    return str((evidence or {}).get("state") or "NO_DRAIN_HAS_EVER_RUN")


def _base(decision: str) -> dict:
    #: Every key every answer carries. ``bound``, ``charged_minor`` and
    #: ``basis_cost_minor`` are here rather than only on the decisions that looked
    #: at them, for the reason the original four were: a caller that has to ask
    #: whether a key exists before reading it will eventually read a missing key as
    #: a meaningful value. ``False``/``None`` here means "this decision did not get
    #: far enough to find out", which is exactly true of a NOT_APPLICABLE answer.
    return {"decision": decision, "reason": "", "message": "", "code": "",
            "unverified": False, "evidence_state": "", "sync_state": "",
            "confirmation": "", "confirmation_age_seconds": None,
            "bound": False, "charged_minor": None, "basis_cost_minor": None}


def _not_applicable(reason: str) -> dict:
    return {**_base(NOT_APPLICABLE), "reason": reason}


def _allow(*, unverified: bool, **extra) -> dict:
    return {**_base(DECISION_ALLOW), "unverified": bool(unverified), **extra}


def _refuse(reason: str, **extra) -> dict:
    return {**_base(DECISION_REFUSE), "reason": reason, "code": reason,
            "message": MESSAGES[reason], **extra}


def _key(listing_id: Any) -> int | None:
    try:
        return int(listing_id)
    except (TypeError, ValueError):
        return None


def _by_listing(prices: Mapping[Any, Any] | None) -> dict[int, Any]:
    """Re-key a lane's price map by integer listing id.

    The lanes hold listing ids in whatever type their own query returned —
    PostgreSQL gives ints, SQLite can give strings, and a route that round-tripped
    one through JSON has a string either way. A map keyed by ``"35"`` that is
    looked up with ``35`` misses silently, and the consequence of a silent miss
    here is that the gate falls back to the catalogue price and never sees the
    stale snapshot it was handed. So the coercion happens once, at the boundary,
    rather than being assumed at the lookup.

    Unreadable keys are dropped rather than raised on: a lane that passes junk
    should lose the precision this parameter buys, not the checkout.
    """
    out: dict[int, Any] = {}
    for listing_id, amount in (prices or {}).items():
        key = _key(listing_id)
        if key is not None:
            out[key] = amount
    return out


def screen(cur, listing_ids: Sequence[Any], *,
           prices: Mapping[Any, Any] | None = None, now: Any = None) -> dict:
    """The whole basket, one answer. The only entry point a checkout lane calls.

    Returns ``{"refusal", "refused_listing_id", "decisions"}``. ``refusal`` is None
    when every line may be charged for; otherwise it is the first refusing
    decision, already carrying the buyer's message and wire code.

    ``prices`` maps listing id to the per-unit amount this checkout is about to
    charge, and is how the loss check sees the number that matters. Optional, and
    the fallback is safe rather than absent: a lane that passes nothing is judged
    against the bound variant's stored retail, so a new lane that forgets this
    parameter is still covered — it just cannot catch the case where a cart's
    snapshot has drifted from the catalogue. Supplying it is strictly better and
    costs the lane nothing, since every lane already holds the figure it is about
    to charge.

    This exists so the three lanes do not each grow their own copy of the loop.
    The cart settles many lines, the offers and buy-now lanes settle one, and the
    temptation is for each to iterate and interpret in its own way — which is how
    the three of them end up disagreeing about whether an unverified line is
    allowed, or about which refusal wins when two lines fail differently. §21:
    one authority. A fourth lane gets this function or it gets nothing.

    The drain latch is read **once per checkout**, not once per line. Every line
    of one basket is being judged at one instant against one reconciler, so
    re-reading it per line would let a two-line cart refuse its second line on
    evidence its first line was allowed under.

    First refusal wins, and the loop stops there. A basket that cannot be filled
    is refused whole — the lanes charge per basket, so there is no partial outcome
    to report, and continuing would only collect reasons nobody will read.
    """
    evidence = reconciliation_evidence(cur, now=now)
    quoted = _by_listing(prices)
    decisions: dict[int, dict] = {}
    for listing_id in listing_ids:
        decision = evaluate(cur, listing_id=listing_id, evidence=evidence, now=now,
                            price_minor=quoted.get(_key(listing_id)))
        try:
            decisions[int(listing_id)] = decision
        except (TypeError, ValueError):
            pass
        if decision["decision"] == DECISION_REFUSE:
            return {"refusal": decision, "refused_listing_id": listing_id,
                    "decisions": decisions}
    return {"refusal": None, "refused_listing_id": None, "decisions": decisions}


def refusal_code(decision: Mapping[str, Any]) -> str:
    """The wire code for a refusal — the client's vocabulary, not the internal one.

    See :data:`WIRE_CODES` for why the two differ.
    """
    reason = str(decision.get("reason") or "")
    return WIRE_CODES.get(reason, reason)


def audit_for(screened: Mapping[str, Any], listing_id: Any) -> dict:
    """The audit annotation for one line of a screened basket.

    A convenience so a lane writing per-line transaction metadata does not have to
    know how ``screen`` keys its decisions.
    """
    try:
        key = int(listing_id)
    except (TypeError, ValueError):
        return {}
    return audit((screened.get("decisions") or {}).get(key) or {})


def audit(decision: Mapping[str, Any]) -> dict:
    """The part of a decision worth storing on the transaction it allowed.

    Only the allowed-but-unverified case produces anything. A refusal never
    reaches a transaction row, and a fully confirmed sale needs no annotation —
    writing one for every order would bury the handful that matter.

    Carries no provider name, connection id, cost or credential (§27). It records
    *that* the sale went through without a current supplier confirmation and what
    the reconciler's state was, which is what a post-mortem needs and the most a
    buyer-facing row should hold.

    ``confirmation`` is on here because the two unverified cases need telling apart
    after the fact. "The reconciler had not reached this listing yet" is expected
    and self-clearing; "the reconciler was stalled" or "was never deployed" is an
    incident. Both produce ``unverified`` sales, and without this key a post-mortem
    counting them cannot say which kind it is looking at. It is one of three fixed
    words (:data:`CONFIRMATION_STATES`), so it leaks nothing.
    """
    if decision.get("decision") != DECISION_ALLOW or not decision.get("unverified"):
        return {}
    return {"supplier_unverified": {
        "reconciliation": decision.get("evidence_state") or "",
        "confirmation": decision.get("confirmation") or "",
        "sync_state": decision.get("sync_state") or "",
    }}
