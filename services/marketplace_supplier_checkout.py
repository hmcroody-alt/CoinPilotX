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
``supplier_worker`` is not in the Procfile, ``CJ_RECONCILIATION_ENABLED`` is not
set, and ``link_source`` never writes ``last_synced_at`` — so on this deployment
every drop-shipped listing has a NULL confirmation and always will, until the
reconciler is actually deployed.

A gate that demanded freshness anyway would take every drop-shipped listing off
sale the moment it shipped. That is not this gate catching a real problem; it is
a change in *what we check* wearing the costume of a change in *what is true*.
Nothing about those listings got worse when this file was added.

So the strictness follows the evidence that exists, read from the reconciler's
own drain latch rather than from a flag somebody has to remember to set:

* **The reconciler has never run.** Freshness cannot be demanded, because it was
  never on offer. Positively-bad state still refuses — a variant the supplier has
  said is sold out is sold out whether or not anything re-read it since. Anything
  else is allowed *and marked*: the decision carries ``unverified`` with the
  latch state that caused it, so the lane records it on the transaction and the
  gap is auditable instead of absorbed.
* **The reconciler is running.** Freshness is demanded. A confirmation older than
  :data:`CONFIRMATION_MAX_AGE_SECONDS`, or a source the last read left ``STALE``,
  refuses — because now silence means something broke, not that the feature was
  never turned on.

The effect is that deploying the worker makes this gate strict by itself, and
until then it says out loud that it cannot vouch for what it is letting through.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from services import marketplace_supplier_schema as supplier_schema
from services import marketplace_variants as variants
from services.business_os.suppliers.fulfillment import DRAIN_STALL_SECONDS

__all__ = [
    "CONFIRMATION_MAX_AGE_SECONDS",
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


def _age_seconds(stamp: Any, now: datetime) -> float | None:
    parsed = _parse(stamp)
    if parsed is None:
        return None
    return (now - parsed).total_seconds()


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

    rows = _orderable(variants.variants_for(cur, int(source["listing_id"])))
    states = [str(row.get("stock_state") or "").strip().upper() for row in rows]
    if states and all(state == supplier_schema.STOCK_OUT_OF_STOCK for state in states):
        # Positively bad, and true regardless of freshness. A supplier that said
        # "none left" has not become less sold out by nobody asking again since.
        return _refuse(REASON_SOLD_OUT, evidence_state=_state_of(evidence))

    running = bool((evidence or {}).get("running"))
    age = _age_seconds(source.get("last_synced_at"), now_dt)
    sync_state = str(source.get("sync_state") or "").strip().upper()
    if not running:
        # See the module docstring: freshness cannot be demanded of a reconciler
        # that has never run. Allowed, and said out loud.
        return _allow(unverified=True, evidence_state=_state_of(evidence),
                      confirmation_age_seconds=age, sync_state=sync_state)
    if age is None or age > CONFIRMATION_MAX_AGE_SECONDS or age < -CLOCK_SKEW_TOLERANCE_SECONDS:
        return _refuse(REASON_STALE_CONFIRMATION, evidence_state=_state_of(evidence),
                       confirmation_age_seconds=age, sync_state=sync_state)
    if sync_state in (supplier_schema.SYNC_STALE, supplier_schema.SYNC_ERROR,
                      supplier_schema.SYNC_DISCONNECTED, supplier_schema.SYNC_REMOVED):
        # A recent *attempt* that failed. The timestamp above only proves when a
        # read last succeeded; this proves the most recent one did not, which is
        # the state a listing sits in while its supplier is unreachable.
        return _refuse(REASON_STALE_CONFIRMATION, evidence_state=_state_of(evidence),
                       confirmation_age_seconds=age, sync_state=sync_state)
    return _allow(unverified=False, evidence_state=_state_of(evidence),
                  confirmation_age_seconds=age, sync_state=sync_state)


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
            "confirmation_age_seconds": None}


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
    """
    if decision.get("decision") != DECISION_ALLOW or not decision.get("unverified"):
        return {}
    return {"supplier_unverified": {
        "reconciliation": decision.get("evidence_state") or "",
        "sync_state": decision.get("sync_state") or "",
    }}
