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
``supplier_worker`` now has a Procfile entry, but a process existing is not the
same as a reconciler running. ``run_tick`` returns ``{"status": "disabled"}``
until ``CJ_RECONCILIATION_ENABLED`` is set, and ``worker.run_once`` — the only
thing that writes the drain latch — sits behind ``policy.require_network()`` as
well, so with ``CJ_NETWORK_ENABLED`` unset the tick returns ``deferred`` and the
latch still never moves. Both are unset in production. ``link_source`` also never
writes ``last_synced_at``.

So on this deployment every drop-shipped listing has a NULL confirmation and a
latch that reads ``NO_DRAIN_HAS_EVER_RUN``, exactly as before the Procfile entry
existed. The entry removes one of three preconditions; it does not give this gate
teeth, and anyone reading the Procfile alone will conclude otherwise. Every
drop-shipped sale today goes through tier 2 as ``unverified``, annotated, and that
is the most this gate can honestly say until something is re-reading.

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
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from services import marketplace_supplier_schema as supplier_schema
from services import marketplace_variants as variants
from services.business_os.suppliers.fulfillment import DRAIN_STALL_SECONDS

__all__ = [
    "CONFIRMATION_KNOWN",
    "CONFIRMATION_MAX_AGE_SECONDS",
    "CONFIRMATION_NEVER",
    "CONFIRMATION_STATES",
    "CONFIRMATION_UNREADABLE",
    "DECISION_ALLOW",
    "DECISION_REFUSE",
    "NOT_APPLICABLE",
    "REASON_SOLD_OUT",
    "REASON_STALE_CONFIRMATION",
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

#: The only two codes a lane may return from this gate. Enumerated so the client
#: strings and the tests are written against one list.
REFUSAL_CODES = (REASON_SOLD_OUT, REASON_STALE_CONFIRMATION)

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
WIRE_CODES = {REASON_SOLD_OUT: "OUT_OF_STOCK"}

#: What the buyer reads. Neither names the supplier, the provider or the
#: connection — §27 applies to a refusal as much as to a success, and "our
#: supplier CJ is down" tells a buyer something about the merchant's business
#: that the merchant did not choose to publish.
MESSAGES = {
    REASON_SOLD_OUT: "This item just went out of stock. You have not been charged.",
    REASON_STALE_CONFIRMATION: (
        "We can't confirm this item is still available right now. "
        "You have not been charged — please try again shortly."),
}

#: Latch states from ``business_os.suppliers.fulfillment.drain_status`` that mean
#: the reconciler is genuinely producing fresh confirmations. ``DRAIN_STALLED``
#: and ``TICKING_BUT_NOT_COMPLETING`` are deliberately absent: a worker that is
#: deployed but broken has stopped refreshing anything, so demanding freshness of
#: it would refuse every checkout for as long as the incident lasted. Those
#: states fall back to the unverified path, which is the same answer as "not
#: deployed" because it is the same situation — nothing is re-reading.
RUNNING_STATES = ("DRAINING",)

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
    else:
        state = "DRAINING"
    return {"state": state, "running": state in RUNNING_STATES,
            "completed_at": completed}


def _orderable(rows: Sequence[Mapping[str, Any]]) -> list[dict]:
    return [dict(row) for row in rows
            if str(row.get("status") or "active").strip().lower() == "active"]


def evaluate(cur, *, listing_id: Any, evidence: Mapping[str, Any] | None = None,
             now: Any = None) -> dict:
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

    Which variant is likewise not asked, because no lane knows: PulseSoc has no
    buyer-side variant selection yet, so a multi-variant listing arrives here
    with nothing chosen. A sell-out therefore has to mean *every* active variant
    is out of stock — if one is still orderable then something on this listing can
    be sold, and refusing would be a guess about which one the buyer wanted. That
    is the same asymmetry ``normalize`` applies across warehouses, and for the
    same reason: out-of-stock is the claim nobody escalates, so it needs the
    strongest evidence.
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
    states = [str(row.get("stock_state") or "").strip().upper() for row in rows]
    if states and all(state == supplier_schema.STOCK_OUT_OF_STOCK for state in states):
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
    return {"decision": decision, "reason": "", "message": "", "code": "",
            "unverified": False, "evidence_state": "", "sync_state": "",
            "confirmation": "", "confirmation_age_seconds": None}


def _not_applicable(reason: str) -> dict:
    return {**_base(NOT_APPLICABLE), "reason": reason}


def _allow(*, unverified: bool, **extra) -> dict:
    return {**_base(DECISION_ALLOW), "unverified": bool(unverified), **extra}


def _refuse(reason: str, **extra) -> dict:
    return {**_base(DECISION_REFUSE), "reason": reason, "code": reason,
            "message": MESSAGES[reason], **extra}


def screen(cur, listing_ids: Sequence[Any], *, now: Any = None) -> dict:
    """The whole basket, one answer. The only entry point a checkout lane calls.

    Returns ``{"refusal", "refused_listing_id", "decisions"}``. ``refusal`` is None
    when every line may be charged for; otherwise it is the first refusing
    decision, already carrying the buyer's message and wire code.

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
    decisions: dict[int, dict] = {}
    for listing_id in listing_ids:
        decision = evaluate(cur, listing_id=listing_id, evidence=evidence, now=now)
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
