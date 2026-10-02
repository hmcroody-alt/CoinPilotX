"""The tick. One instance, one decision, one row in the log — or nothing at all.

This is the module the brief describes as *not* ``SELECT random_product; POST;
sleep(2h)``, and the difference is almost entirely in what it declines to do.

## The shape of a tick

Read the catalog once, judge every candidate, rank them, load PulseDrop's own
recent history once, and then walk the ranking from the top asking a single
question per candidate: *is there a format that should publish this, now?* The
first candidate that answers yes is published and the tick stops. Every
candidate that answers no contributes a counted reason and the walk continues.
If nobody answers yes, nothing is published and the run row says why — which is
the whole point of having a run row.

## Why the read connection is closed before anything is published

Publication is three separate writers — the claim, ``create_post`` (which opens
and commits its own connection by design), and the settle — and holding the
curator's read connection across all of them would keep one of eight pool slots
occupied for the length of a moderation call. This repo has emptied that pool
before, by exactly this shape. So the read phase ends, explicitly, before the
publish phase begins; nothing from the snapshot is needed afterwards except the
one chosen candidate, and the publisher re-reads that under its own connection
anyway.

## Why pacing deferrals stop the walk and cooldowns do not

``min_publish_interval`` and ``daily_cap`` are properties of the *account*: when
they are true, no candidate is publishable and testing the next forty is work
whose answer cannot change. A product cooldown is a property of one product, so
the walk moves on to the next. The distinction is the difference between a tick
that does forty pointless comparisons and one that does none.

## Why rendering does not happen here

An encode is minutes and the lease is five, so a curator that rendered inline
would routinely outlive its own lease and hand a second instance the same tick.
The tick therefore *enqueues* and returns; :func:`worker_cycle` drains the queue
outside the lease, where taking ten minutes is nobody's problem. The visible
consequence is that a Reel publishes on the tick after the one that chose it,
which no member can perceive and which keeps the mutual exclusion honest.

## Why "disabled" and "not due" write nothing

The worker loop runs every few seconds. A run row per loop would be a hundred
thousand rows a day describing a system that did nothing, and it would bury the
handful of rows that describe a system that did something. Both exits are
therefore silent, and both are still observable — ``config.enabled()`` and the
lease row answer them directly.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime

from services.pulsedrop import (
    account,
    campaigns,
    config,
    distribution,
    diversity,
    eligibility,
    lease,
    publisher,
    ranking,
    reel_composer,
    schema,
)

log = logging.getLogger(__name__)

#: Outcomes, stable and countable. An operator asking "why has PulseDrop not
#: posted" should be able to answer it with one ``GROUP BY outcome``.
DISABLED = "disabled"
NOT_DUE = "not_due"
PUBLISHED = "published"
NO_CANDIDATES = "no_candidates"
NONE_ELIGIBLE = "none_eligible"
DEFERRED = "deferred"
SKIPPED = "skipped"
RENDER_PENDING = "render_pending"
FAILED = "failed"
ERROR = "error"
#: Pulse Loop only. One surface of a scheduled pair landed and the other did
#: not -- distinct from ``PUBLISHED`` because the promise was a pair, and
#: distinct from ``FAILED`` because something did reach members.
PARTIAL = "partial"
#: Pulse Loop only, and the outcome the brief asks for by name: every eligible
#: product already has a campaign in this generation and it is not yet time to
#: start another. Its own outcome rather than a flavour of "nothing happened",
#: because a loop that is working perfectly and has run out of products to talk
#: about must not look like a loop that is broken.
SUPPLY_LIMITED = "catalog_supply_limited"

#: Deferrals that are true of the account rather than of a product, and so end
#: the walk instead of advancing it. See the module docstring.
ACCOUNT_LEVEL_DEFERRALS = frozenset({"min_publish_interval", "daily_cap"})

#: Accounts whose listings PulseDrop must never republish. ``0`` is
#: ``pulsesoc_insight``, the pre-existing system account; PulseDrop's own id is
#: added at run time. A curator that can select its own output has a feedback
#: loop, and this is where that is prevented rather than hoped against.
_BASE_SYSTEM_USER_IDS = (0,)


def worker_cycle(*, now: datetime | None = None) -> dict:
    """One pass of everything PulseDrop wants a background process to do.

    The single entry point a worker should call. Order matters: renders drain
    *first*, so a Reel whose encode finished during this cycle is publishable by
    the tick in the same cycle rather than waiting for the next one.

    Both halves are independently safe to run on every instance — the drain
    claims rows one at a time, the tick takes a lease — so this needs no
    scheduling of its own beyond being called.
    """
    renders = {"started": 0, "succeeded": 0, "failed": 0}
    if not config.enabled():
        return {"outcome": DISABLED, "renders": renders}
    try:
        renders = reel_composer.run_pending(limit=1, now=now)
    except Exception:
        # A renderer that throws must not stop the curator from publishing the
        # Signals that need no renderer at all.
        log.warning("pulsedrop_render_drain_failed", exc_info=True)
    result = tick(now=now, render_counters=renders)
    result["renders"] = renders
    return result


def tick(*, now: datetime | None = None, render_counters: dict | None = None) -> dict:
    """Evaluate once and publish at most once. Never raises.

    ``render_counters`` are what the caller's drain achieved this cycle; they are
    recorded on the run row so that "PulseDrop published nothing and three
    renders failed" is a single row rather than a log search.
    """
    moment = now or datetime.utcnow()
    if not config.enabled():
        # Deliberately does not push ``next_run_at`` out. An operator turning the
        # switch back on wants the next cycle to act, not to discover that the
        # curator scheduled itself two hours into the future while it was off.
        return {"outcome": DISABLED}

    holder = lease.acquire(now=moment)
    if not holder:
        return {"outcome": NOT_DUE}

    result = {"outcome": ERROR, "reason": "unhandled"}
    try:
        result = _execute(moment, render_counters or {})
    except Exception:
        log.exception("pulsedrop_tick_failed")
    finally:
        lease.release(holder, now=moment, next_run_in_seconds=_next_run_seconds(result))
    return result


def _next_run_seconds(result: dict) -> int:
    """How long until the next evaluation, given how this one ended.

    A tick that is waiting on an encode comes back sooner than one that decided
    nothing was worth publishing — the render will be finished in a minute or
    two, and sitting on a ready Reel for two hours is latency with no purpose.
    Coming back early is safe because it is not a licence to publish: the
    interval and cap rules are evaluated against the publication history, not
    against the tick counter, so a short re-run that finds nothing publishable
    simply defers again.
    """
    if result.get("outcome") == RENDER_PENDING:
        return max(60, int(config.reel_render_timeout_seconds()))

    interval = int(config.evaluation_interval_seconds())
    if not config.loop_enabled():
        return interval

    # With a schedule, the evaluation interval is the wrong clock. It is a
    # sampling rate for "go and look at the catalog", and two hours is sensible
    # for that; a campaign has an exact due time, and sampling every two hours
    # for events ninety minutes apart publishes everything late by up to the
    # difference. So wake when there is something to do.
    #
    # Capped at the interval, not replaced by it: an unreadable or empty
    # schedule must not wake the curator every minute forever, and `None` from
    # `seconds_until_next_due` covers both of those cases on purpose. Floored at
    # 60 because waking sooner than that cannot publish anything the minimum gap
    # between posts would allow.
    try:
        due_in = campaigns.seconds_until_next_due()
    except Exception:
        log.debug("pulsedrop_next_due_failed", exc_info=True)
        return interval
    if due_in is None:
        return interval
    return max(60, min(due_in, interval))


def _execute(now: datetime, render_counters: dict) -> dict:
    from services import db as db_service

    schema.ensure_schema()
    started = datetime.utcnow()
    run_id = uuid.uuid4().hex

    # Claims left behind by a crashed instance are closed at the top of the tick
    # rather than lazily: a stuck 'claimed' row is what makes a product look
    # published when it is not, and the cheapest moment to notice is now.
    reclaimed = publisher.reap_stale_claims(now=now)
    if reclaimed:
        log.info("pulsedrop_reaped_stale_claims count=%s", reclaimed)

    pulsedrop_user_id = account.ensure_account()
    system_ids = set(_BASE_SYSTEM_USER_IDS) | {int(pulsedrop_user_id or 0)}

    if config.loop_enabled():
        return _execute_loop(run_id, now, started, system_ids, render_counters)

    conn = None
    try:
        conn = db_service.connect()
        cur = conn.cursor()
        candidates = eligibility.fetch_candidates(cur, config.candidate_limit(), now=now)
        eligible, rejected = eligibility.partition(candidates, system_user_ids=system_ids)
        if not eligible:
            outcome = NO_CANDIDATES if not candidates else NONE_ELIGIBLE
            return _record(
                run_id, outcome, diversity.most_specific(set(rejected)), started,
                evaluated=len(candidates), rejected=rejected, render_counters=render_counters,
            )
        distinct_sellers = len({int(item.get("seller_user_id") or 0) for item in eligible})
        # A history that cannot be read is not an empty history: treating it as
        # one would drop every cooldown at once. ``load`` re-raises, and the
        # tick abandons — PulseDrop publishing nothing is always recoverable.
        history = diversity.History.load(
            cur, now, distinct_eligible_sellers=distinct_sellers
        )
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass

    ranked = ranking.rank(eligible, now=now)
    chosen, decision, declines = _choose(ranked, history)

    if chosen is None:
        outcome = SKIPPED if _all_structural(declines) else DEFERRED
        return _record(
            run_id, outcome, diversity.most_specific(set(declines)), started,
            evaluated=len(candidates), eligible=len(eligible), rejected=rejected,
            declines=declines, render_counters=render_counters,
        )

    return _publish(
        run_id, chosen, decision, started, now,
        evaluated=len(candidates), eligible=len(eligible),
        rejected=rejected, declines=declines, render_counters=render_counters,
    )


def _execute_loop(run_id, now, started, system_ids, render_counters) -> dict:
    """One tick of Pulse Loop: heal, prepare, publish what is due, replenish.

    The same four phases in the same order every tick, because the order is
    where the correctness lives:

    1. **Heal.** Reclaim campaigns a crashed instance left mid-publish. First,
       for the reason the opportunistic path reaps its claims first — a stuck
       row is indistinguishable from work in progress, and the cheapest moment
       to notice is before deciding anything.
    2. **Prepare.** Enqueue Reel encodes for campaigns that are about to come
       due. This is the phase that makes a *pair* possible: the encode happens
       while nothing is waiting on it.
    3. **Publish.** At most one campaign, claimed row-level inside the lease.
    4. **Replenish, then enroll.** After publishing, so that a campaign
       released because the catalog withdrew its product frees its slot in the
       same tick that noticed. Replenishment is cheap when there is nothing to
       do: two counting queries against an index, and the watermark returns
       before any catalog read. Enrollment runs only when replenishment
       declined — a deep queue is the one state in which a product can be in
       the catalog and in no generation at all, because depth cannot answer
       "is this product enrolled". See the call site.

    Every phase is wrapped, because a tick that heals and then throws on
    prewarm should still publish. The run row is written once, at the end, by
    the same ``_record`` the opportunistic path uses: an operator asking why
    PulseDrop has not posted should not have to know which mode it was in to
    know where to look.
    """
    try:
        campaigns.reap_stale_claims(now=now)
    except Exception:
        log.warning("pulsedrop_campaign_reap_failed", exc_info=True)

    prepared = {}
    try:
        prepared = campaigns.prewarm(now=now)
    except Exception:
        log.warning("pulsedrop_campaign_prewarm_failed", exc_info=True)

    published = {"outcome": campaigns.NONE_DUE}
    try:
        published = campaigns.execute_due(now=now, system_user_ids=system_ids)
    except Exception:
        log.exception("pulsedrop_campaign_execute_failed")
        published = {"outcome": "ERROR", "reason": "execute_due raised"}

    planned = {}
    try:
        planned = campaigns.plan(now=now, system_user_ids=system_ids)
        if str(planned.get("outcome") or "") == campaigns.QUEUE_SATISFIED:
            # The watermark declined to top up, which is the one state in which
            # a product could be in the catalog and in no generation at all --
            # so ask the narrower question before moving on. `reconcile` writes
            # only campaigns for products with no campaign this cycle, so a
            # deep queue is left exactly as it is and this is a no-op unless the
            # catalog has actually grown.
            #
            # Note what this costs, because it is less than it looks. The reads
            # are the two the watermark just skipped, so the saving is halved
            # rather than spent; in exchange a newly listed product is enrolled
            # on the next tick instead of waiting for a ten-day horizon to
            # drain. And it is reached rarely: a catalog smaller than the target
            # depth can never satisfy the watermark, so on PulseSoc's actual
            # supply this branch does not run at all and enrollment is already
            # prompt by way of ordinary replenishment.
            enrolled = campaigns.reconcile(now=now, system_user_ids=system_ids)
            if int(enrolled.get("planned") or 0) > 0:
                planned = enrolled
    except Exception:
        log.warning("pulsedrop_campaign_plan_failed", exc_info=True)

    outcome, reason = _loop_outcome(published, planned)
    return _record(
        run_id, outcome, reason, started,
        evaluated=int(planned.get("evaluated") or 0),
        eligible=int(planned.get("eligible") or 0),
        rejected=planned.get("rejected") or {},
        selected_listing_id=int(published.get("listing_id") or 0),
        decision=_loop_decision(published, planned),
        post_id=int(published.get("signal_post_id") or 0),
        reel_post_id=int(published.get("reel_post_id") or 0),
        renders_started=int(prepared.get("enqueued") or 0),
        render_counters=render_counters,
    )


#: How a campaign outcome is reported in the run log. The left side is the
#: campaign state machine's vocabulary; the right is the curator's, which
#: predates it and which the admin page and every existing ``GROUP BY outcome``
#: already speak. Translating here rather than inventing a parallel vocabulary
#: is what keeps "why has PulseDrop not posted" answerable with one query
#: regardless of which mode produced the row.
#:
#: Two mappings are worth justifying. ``AWAITING_RENDER`` becomes
#: ``RENDER_PENDING`` because it is the same fact, and because
#: ``_next_run_seconds`` already shortens the next tick for that outcome — the
#: behaviour we want, inherited rather than re-implemented. ``PACED`` becomes
#: ``DEFERRED`` with a reason drawn from ``ACCOUNT_LEVEL_DEFERRALS``, so a cap
#: that stops the loop counts as the same thing as a cap that stops the walk.
_LOOP_OUTCOMES: dict[str, str] = {
    campaigns.PUBLISHED: PUBLISHED,
    campaigns.PARTIAL: PARTIAL,
    campaigns.FAILED: FAILED,
    campaigns.RELEASED: SKIPPED,
    campaigns.AWAITING_RENDER: RENDER_PENDING,
    campaigns.PACED: DEFERRED,
    campaigns.LOST_CLAIM: DEFERRED,
    campaigns.DISABLED: DISABLED,
}

#: And the same for the planner, consulted only when nothing was due.
_PLAN_OUTCOMES: dict[str, str] = {
    campaigns.PLANNED: NOT_DUE,
    campaigns.QUEUE_SATISFIED: NOT_DUE,
    campaigns.NO_CANDIDATES: NO_CANDIDATES,
    campaigns.SUPPLY_LIMITED: SUPPLY_LIMITED,
    campaigns.DISABLED: DISABLED,
}


def _loop_outcome(published: dict, planned: dict) -> tuple[str, str]:
    """``(outcome, reason)`` for the run row, given both phases' results.

    The publication is the headline when there was one. When nothing was due,
    the planner's answer is the interesting fact — and specifically
    ``CATALOG_SUPPLY_LIMITED`` is, which is why it gets its own outcome rather
    than being flattened into "nothing happened". A loop that is working
    perfectly and has run out of products to talk about looks identical to a
    broken loop in any log that does not make that distinction.
    """
    state = str(published.get("outcome") or "")
    if state and state != campaigns.NONE_DUE:
        mapped = _LOOP_OUTCOMES.get(state)
        if mapped:
            return mapped, str(published.get("reason") or state)
        return ERROR, str(published.get("reason") or state)[:120]

    plan_state = str(planned.get("outcome") or "")
    mapped = _PLAN_OUTCOMES.get(plan_state)
    if mapped == NOT_DUE:
        count = int(planned.get("planned") or 0)
        return NOT_DUE, (f"planned_{count}" if count else "queue_satisfied")
    if mapped:
        return mapped, str(planned.get("reason") or plan_state)[:120]
    return NOT_DUE, plan_state or "idle"


def _loop_decision(published: dict, planned: dict) -> str:
    """The ``decision`` column: which surfaces a published campaign reached.

    Reuses the free-text column the opportunistic path writes its format
    decision into, so the two modes remain comparable in the run log. For a tick
    that published nothing it records the horizon depth instead, because that is
    the one number that makes an idle tick readable: "nothing was due and there
    are 212 scheduled" and "nothing was due and there are 0" are the same
    outcome and completely different situations.
    """
    state = str(published.get("outcome") or "")
    if state in (campaigns.PUBLISHED, campaigns.PARTIAL):
        surfaces = []
        if published.get("signal_post_id"):
            surfaces.append("signal")
        if published.get("reel_post_id"):
            surfaces.append("reel")
        return "+".join(surfaces) or "none"
    depth = planned.get("depth")
    return f"depth={int(depth)}" if depth is not None else ""


def _choose(ranked, history):
    """Walk the ranking for the first candidate a format wants to publish.

    Returns ``(ranked_item, decision, declines)``. ``declines`` is the histogram
    of everything that said no, which is what the run row reports when nothing
    said yes.
    """
    declines: dict[str, int] = {}
    for item in ranked:
        decision = distribution.decide(item, history)
        if decision.publishes_signal or decision.publishes_reel:
            return item, decision, declines
        declines[decision.reason] = declines.get(decision.reason, 0) + 1
        if decision.outcome == distribution.DEFER and decision.reason in ACCOUNT_LEVEL_DEFERRALS:
            # True of PulseDrop, not of this product. Nothing below can pass.
            break
    return None, None, declines


def _all_structural(declines: dict) -> bool:
    """Whether every refusal was a SKIP-shaped one.

    A run where the only reasons are structural — no media, no enabled surface,
    no encoder — will keep being true until a listing or the deployment changes,
    and is worth distinguishing from one that is merely waiting out a cooldown.
    """
    structural = {"no_publishable_format", "both_surfaces_disabled", "no_enabled_format"}
    return bool(declines) and set(declines) <= structural


def _publish(run_id, chosen, decision, started, now, **counts) -> dict:
    """Act on a decision. The only place in the tick that writes to the feed."""
    listing = chosen.listing
    listing_id = int(listing.get("id") or 0)
    post_id = 0
    reel_post_id = 0
    reasons: list[str] = []
    render_enqueued = 0

    if decision.publishes_reel:
        render = reel_composer.find_or_enqueue(listing, decision.reel_source, now=now)
        state = str(render.get("state") or "")
        if state != reel_composer.READY or not render.get("video_url"):
            if state == reel_composer.PENDING and not int(render.get("attempts") or 0):
                render_enqueued = 1
            if not decision.publishes_signal:
                # Nothing else this candidate can do this tick. Not a failure —
                # the encode is queued and the next tick will find it.
                return _record(
                    run_id, RENDER_PENDING, f"render_{state or 'unavailable'}", started,
                    selected_listing_id=listing_id, decision=decision.outcome,
                    renders_started=render_enqueued, **counts,
                )
            reasons.append(f"reel_render_{state or 'unavailable'}")
        else:
            result = publisher.publish_reel(chosen, render, now=now)
            if result.ok:
                reel_post_id = result.post_id
            else:
                reasons.append(f"reel_{result.reason}")

    if decision.publishes_signal:
        result = publisher.publish_signal(chosen, now=now)
        if result.ok:
            post_id = result.post_id
        else:
            reasons.append(f"signal_{result.reason}")

    published = bool(post_id or reel_post_id)
    return _record(
        run_id,
        PUBLISHED if published else FAILED,
        ",".join(reasons)[:200] if reasons else decision.reason,
        started,
        selected_listing_id=listing_id,
        decision=decision.outcome,
        post_id=post_id,
        reel_post_id=reel_post_id,
        renders_started=render_enqueued,
        **counts,
    )


def _record(run_id, outcome, reason, started, *, evaluated=0, eligible=0, rejected=None,
            declines=None, selected_listing_id=0, decision="", post_id=0, reel_post_id=0,
            renders_started=0, render_counters=None) -> dict:
    """Write the run row and return the same facts to the caller.

    The row and the return value are built from one dict on purpose: a caller
    that logs something the table does not contain produces two accounts of the
    same tick, and the one an operator reads later is the table.
    """
    counters = dict(render_counters or {})
    # The rejection histogram and the decision histogram are merged under
    # distinct prefixes rather than summed. They count different populations —
    # listings that were never eligible, and eligible listings no format wanted —
    # and adding them would make the total meaningless.
    histogram = {f"gate:{k}": v for k, v in (rejected or {}).items()}
    histogram.update({f"decide:{k}": v for k, v in (declines or {}).items()})

    finished = datetime.utcnow()
    payload = {
        "run_id": run_id,
        "outcome": outcome,
        "reason": str(reason or "")[:120],
        "evaluated": int(evaluated),
        "eligible": int(eligible),
        "rejected_json": json.dumps(histogram, sort_keys=True)[:2000],
        "selected_listing_id": int(selected_listing_id),
        "decision": str(decision or ""),
        "post_id": int(post_id),
        "reel_post_id": int(reel_post_id),
        "renders_started": int(renders_started) + int(counters.get("started") or 0),
        "renders_succeeded": int(counters.get("succeeded") or 0),
        "renders_failed": int(counters.get("failed") or 0),
        "duration_ms": int((finished - started).total_seconds() * 1000),
        "started_at": started.isoformat(timespec="seconds"),
        "finished_at": finished.isoformat(timespec="seconds"),
    }
    _write_run(payload)
    log.info(
        "pulsedrop_run outcome=%s reason=%s evaluated=%s eligible=%s listing=%s post=%s reel=%s",
        outcome, payload["reason"], evaluated, eligible, selected_listing_id, post_id, reel_post_id,
    )
    return payload


def _write_run(payload: dict) -> None:
    """Persist the run row. A failure here must not fail the tick.

    The row is evidence about work that already happened; losing it is bad and
    is strictly better than rolling back a publication that succeeded.
    """
    from services import db as db_service

    conn = None
    try:
        conn = db_service.connect()
        cur = conn.cursor()
        columns = list(payload.keys())
        cur.execute(
            f"INSERT INTO pulsedrop_runs ({', '.join(columns)}) "
            f"VALUES ({', '.join('?' for _ in columns)})",
            tuple(payload[column] for column in columns),
        )
        conn.commit()
    except Exception:
        log.warning("pulsedrop_run_record_failed run_id=%s", payload.get("run_id"), exc_info=True)
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def recent_runs(limit: int = 20) -> list[dict]:
    """The last few ticks, newest first. For the admin surface."""
    from services import db as db_service

    schema.ensure_schema()
    conn = None
    try:
        conn = db_service.connect()
        cur = conn.cursor()
        cur.execute(
            "SELECT * FROM pulsedrop_runs ORDER BY id DESC LIMIT ?", (max(1, int(limit)),)
        )
        return [dict(row) for row in cur.fetchall() or []]
    except Exception:
        log.warning("pulsedrop_runs_read_failed", exc_info=True)
        return []
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
