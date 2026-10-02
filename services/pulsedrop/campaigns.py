"""Pulse Loop: the plan, and the promise that the plan is re-checked.

PulseDrop before this module was entirely present-tense. A tick asked "what
should I publish *now*", published it, and recorded that it had. That is a
correct content engine and it cannot answer the two questions Pulse Loop is
for — *what is coming*, and *is a product's pair going out together* — because
neither is a fact about the present moment.

This module adds the future tense. It plans campaigns, each one product in one
cycle, each with a time it is due; the tick executes whichever is due. The whole
design rests on one rule, and everything awkward here follows from it:

## A plan is an intention, never a cached truth

A campaign planned on Monday for Thursday says *"publish listing 42 on
Thursday"*. It must not say "publish listing 42, which costs $19.88 and has 6,282
in stock, on Thursday", because by Thursday any of that may be false and a post
asserting it would be a lie the platform told on a seller's behalf.

So a campaign row stores a ``listing_id`` and a time. It stores no price, no
stock, no availability, no title, no image. Those are read at publish time by
the same hydration path the app and the web already use, which is the
overlay architecture's whole point: pixels are immutable, the reference is
immutable, the commercial facts are read fresh every single time.

The same rule applies one level up, to eligibility itself. Being plannable on
Monday is not permission to publish on Thursday — the seller may have delisted
the product, sold out, had it moderated, or been suspended. So
:func:`execute_due` re-runs the real eligibility gate against the live row
before it publishes anything, and a campaign whose product no longer qualifies
is *released* rather than published or failed. That is the single most important
behaviour in this file. Without it, a scheduling horizon is just a buffer of
increasingly stale claims, and the longer the horizon the worse the lie.

## Why planning and publishing are different phases

Planning is cheap, batched and reads the catalog once. Publishing is expensive,
serial, and touches the feed. Fusing them is what produces an engine that either
scans the whole catalog every minute or publishes without looking. Splitting
them also means the expensive half can be *prepared* in advance: a Reel encode
takes minutes, and because the campaign's due time is known ahead of it, the
render can be enqueued before the moment arrives instead of after. Production
spent 32 of its last 300 runs reporting ``reel_render_pending`` for exactly this
reason, and a known due time is what makes that avoidable.

## Why the pair is one row and not two

Production is the argument. With a Signal and a Reel claiming independently, the
Signals all went out in one burst, hit a 14-day per-product cooldown, and then
the Reels published alone for days — every run row reading ``REEL_ONLY`` /
``signal_unavailable`` — while the operator who had switched on "pair every
post" reasonably believed pairs were happening. Nothing was broken; the two
surfaces simply had no shared unit of work, so there was nothing anywhere that
could notice they had drifted apart.

One row with both target surfaces and both resulting post ids makes the pair a
thing that exists. It also relocates the fairness question: caps and cooldowns
now key on the campaign, so "once per product per cycle" is enforced in one
place instead of being approximated twice with different clocks.

## Why exhaustion is reported and not papered over

When every eligible product already has a campaign in the current cycle, this
module does not invent work. It does not re-plan a product early, duplicate a
listing, or lower a bar to hit a queue-depth number. It reports
:data:`SUPPLY_LIMITED` and stops, because a content engine quietly recycling a
37-product catalog into a hundred scheduled slots is indistinguishable from spam
to the only audience that matters, and the honest signal — "the catalog is the
constraint" — is the one an operator can actually act on.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from services.pulsedrop import config, eligibility, schema

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# States
# ---------------------------------------------------------------------------

#: Planned, not yet due. The resting state and the only one the planner writes.
SCHEDULED = "scheduled"
#: Claimed by an instance that is publishing it right now. A row that stays here
#: past its claim expiry is a crashed attempt and is reclaimable.
PUBLISHING = "publishing"
#: Both wanted surfaces landed.
PUBLISHED = "published"
#: One surface landed and the other did not. Deliberately terminal-but-flagged:
#: the post that exists must not be deleted, and the half that is missing must
#: not be retried blindly days later into a feed where its partner has long
#: scrolled past. See :func:`_settle`.
PARTIAL = "partial"
#: The catalog withdrew the product between planning and publishing. Not an
#: error. The slot is refillable and the product is re-plannable.
RELEASED = "released"
#: We could not publish it and have stopped trying.
FAILED = "failed"

#: States that still occupy a slot in the horizon, and so count towards depth.
OPEN_STATES = (SCHEDULED, PUBLISHING)
#: States a campaign cannot leave.
TERMINAL_STATES = (PUBLISHED, PARTIAL, RELEASED, FAILED)

# ---------------------------------------------------------------------------
# Planner outcomes
# ---------------------------------------------------------------------------

PLANNED = "PLANNED"
#: Depth is already at or above target; nothing to do and nothing wrong.
QUEUE_SATISFIED = "QUEUE_SATISFIED"
#: Every eligible product already has a campaign this cycle and the cycle may
#: not roll yet. The honest answer, and the one the brief asks for by name.
SUPPLY_LIMITED = "CATALOG_SUPPLY_LIMITED"
#: There is nothing publishable in the catalog at all, which is a different
#: problem from having published all of it.
NO_CANDIDATES = "NO_CANDIDATES"
DISABLED = "DISABLED"


def _iso(moment: datetime) -> str:
    return moment.isoformat(timespec="seconds")


def _now(now: datetime | None) -> datetime:
    return now or datetime.utcnow()


def _is_stale(raw, moment: datetime, horizon: timedelta) -> bool:
    """Whether a claim stamped ``raw`` is old enough to be presumed crashed.

    Unparseable and empty both answer True, which is the safe direction here:
    the cost of reclaiming a live claim is a duplicate publish attempt, and the
    publisher's own per-surface idempotency key absorbs that. The cost of
    refusing to reclaim is a horizon slot leaked permanently, with no signal.
    """
    text = str(raw or "").strip()
    if not text:
        return True
    try:
        # Tolerant of both ``T`` and ``' '`` separators by construction.
        claimed = datetime.fromisoformat(text)
    except Exception:
        return True
    if claimed.tzinfo is not None:
        claimed = claimed.replace(tzinfo=None)
    return (moment - claimed) >= horizon


# ---------------------------------------------------------------------------
# Reading the schedule
# ---------------------------------------------------------------------------


def depth(cur, *, cycle: int | None = None) -> int:
    """How many campaigns are planned and not yet resolved.

    The number the replenisher steers on. Counts :data:`OPEN_STATES` rather than
    'scheduled' alone, because a row being published right now is still a slot
    that is spoken for, and excluding it would make the planner top up a queue
    that is merely busy.
    """
    sql = f"SELECT COUNT(*) FROM pulsedrop_campaigns WHERE state IN ({_q(OPEN_STATES)})"
    params: list = list(OPEN_STATES)
    if cycle is not None:
        sql += " AND cycle = ?"
        params.append(int(cycle))
    cur.execute(sql, tuple(params))
    row = cur.fetchone()
    return int(_first(row) or 0)


def current_cycle(cur) -> int:
    """The generation the planner is filling.

    ``MAX(cycle)`` rather than a stored counter: the counter would be a second
    source of truth about the same fact, and the failure mode of those two
    disagreeing is a planner that writes campaigns into a cycle nothing reads.
    """
    cur.execute("SELECT MAX(cycle) FROM pulsedrop_campaigns")
    row = cur.fetchone()
    return max(1, int(_first(row) or 0))


def _q(values) -> str:
    return ", ".join("?" for _ in values)


def next_due(cur, now: datetime) -> dict:
    """The one campaign whose time has come, or ``{}``.

    ``scheduled_for <= now`` rather than ``= now``: a deployment that was down,
    paused, or simply slower than its own cadence comes back to a queue with
    several campaigns already overdue, and the right behaviour is to work
    through them oldest-first rather than to skip to whatever is due now and
    orphan the ones that were missed.
    """
    cur.execute(
        "SELECT * FROM pulsedrop_campaigns WHERE state = ? AND scheduled_for <= ? "
        "ORDER BY scheduled_for ASC, id ASC LIMIT 1",
        (SCHEDULED, _iso(now)),
    )
    row = cur.fetchone()
    return dict(row) if row else {}


def upcoming(cur, limit: int = 10) -> list[dict]:
    """The next ``limit`` campaigns in the order they will publish.

    For the admin surface, and for answering "is the loop actually loaded" with
    rows rather than with a count.
    """
    cur.execute(
        f"SELECT * FROM pulsedrop_campaigns WHERE state IN ({_q(OPEN_STATES)}) "
        "ORDER BY scheduled_for ASC, id ASC LIMIT ?",
        (*OPEN_STATES, max(1, int(limit))),
    )
    return [dict(row) for row in cur.fetchall() or []]


def _first(row):
    """The first column of a row, or None if there was no row.

    Positional ``row[0]`` is deliberate and is the only access used for scalar
    aggregates here. ``services.db.row_values`` documents why: ``sqlite3.Row``
    is a sequence and the Postgres ``CompatRow`` is a Mapping, so ``tuple(row)``
    means *values* on one engine and *column names* on the other — the bug that
    took comm_v2's idempotency preflight down. ``row[0]`` agrees on both.

    The wrapper exists only so the ``None``-vs-``(None,)`` distinction (no row
    at all, versus a ``COUNT`` over an empty table) is handled once.
    """
    return None if row is None else row[0]


# ---------------------------------------------------------------------------
# Planning
# ---------------------------------------------------------------------------


def plan(*, now: datetime | None = None, system_user_ids=(), force: bool = False) -> dict:
    """Top the horizon up towards its target depth. Never publishes.

    Returns a dict with ``outcome``, ``planned``, ``depth`` and ``cycle``. The
    outcome is the interesting part and has four honest answers: we added some
    (:data:`PLANNED`), the queue was already deep enough
    (:data:`QUEUE_SATISFIED`), the catalog has nothing publishable at all
    (:data:`NO_CANDIDATES`), or every eligible product already has a campaign
    this cycle and it is not yet time to start another (:data:`SUPPLY_LIMITED`).

    The fourth is the one the brief singles out, and it is a *success* in the
    sense that the engine did the right thing — it just cannot manufacture
    supply, and saying so is more useful than any of the things it could have
    done instead.

    Replenishment runs on a pair of watermarks rather than a single target:
    nothing is read from the catalog while the queue is above
    :func:`config.loop_min_depth`, and once it reaches that line the queue is
    filled all the way back to :func:`config.loop_target_depth`. Topping up to
    the target on every tick would be equally correct and would spend a
    candidate query and a full ranking pass to schedule one row.

    ``force`` is enrollment rather than replenishment, and the difference is
    not a matter of degree. Replenishment asks "is the queue deep enough", which
    the watermarks answer. Enrollment asks "is this product in the generation at
    all", which they cannot: a deep queue is a perfectly good reason not to top
    up and never a reason to leave a newly listed product out of the cycle
    entirely. So ``force`` both skips the low-water check *and* appends past the
    target depth, because "fill to 240" is a floor on a replenishment pass, not
    a ceiling on the table. Reading it as a ceiling is what would make a product
    listed today wait for a ten-day horizon to drain before it was so much as
    scheduled.

    That stays bounded without needing a cap, which is the part worth noticing.
    ``campaign_key`` permits one campaign per product per generation, so the
    table cannot exceed the eligible catalog however often this runs, and
    :func:`eligibility.fetch_candidates` is already limited by
    :func:`config.candidate_limit`, so no single pass can write more rows than
    that. §7's "do not pregenerate unbounded rows" is held by an invariant here
    rather than by a number someone has to keep correct.
    """
    from services import db as db_service

    moment = _now(now)
    if not config.enabled() or not config.loop_enabled():
        return {"outcome": DISABLED, "planned": 0, "depth": 0, "cycle": 0}

    schema.ensure_schema()
    conn = None
    try:
        conn = db_service.connect()
        cur = conn.cursor()

        cycle = current_cycle(cur)
        have = depth(cur)
        target = config.loop_target_depth()
        # Clamped because a floor above the ceiling is a settable mistake, and
        # the coherent reading of it is "always replenish" rather than "refuse
        # to": an operator who raises the floor is asking for a deeper queue.
        floor = min(config.loop_min_depth(), target)
        if not force and (have >= target or have > floor):
            return {
                "outcome": QUEUE_SATISFIED, "planned": 0, "depth": have,
                "cycle": cycle, "target_depth": target, "min_depth": floor,
            }

        candidates = eligibility.fetch_candidates(cur, config.candidate_limit(), now=moment)
        eligible, rejected = eligibility.partition(candidates, system_user_ids=system_user_ids)
        if not eligible:
            return {
                "outcome": NO_CANDIDATES, "planned": 0, "depth": have, "cycle": cycle,
                "evaluated": len(candidates), "rejected": rejected,
            }

        # A product gets one campaign per cycle. 'Taken' is every state except
        # `released` -- a released campaign did not publish, so holding its
        # product out of the cycle would punish the product for the catalog's
        # churn and slowly starve the loop of exactly the items most likely to
        # come back in stock.
        taken = _listings_in_cycle(cur, cycle)
        fresh = [item for item in eligible if int(item.get("id") or 0) not in taken]

        if not fresh:
            rolled = _maybe_roll_cycle(cur, conn, cycle, eligible, moment)
            if rolled is None:
                return {
                    "outcome": SUPPLY_LIMITED, "planned": 0, "depth": have, "cycle": cycle,
                    "eligible": len(eligible), "cycle_complete": True,
                    "reason": "every eligible product already has a campaign in this cycle",
                }
            cycle = rolled
            taken = _listings_in_cycle(cur, cycle)
            fresh = [item for item in eligible if int(item.get("id") or 0) not in taken]
            if not fresh:
                return {
                    "outcome": SUPPLY_LIMITED, "planned": 0, "depth": have, "cycle": cycle,
                    "eligible": len(eligible), "reason": "new cycle is already full",
                }

        from services.pulsedrop import ranking

        ranked = ranking.rank(fresh, now=moment)
        ordered = _declustered(ranked)
        # Enrollment takes everything new; replenishment takes only what fits
        # under the target. See the docstring for why the target is a floor on a
        # pass rather than a ceiling on the table.
        room = len(ordered) if force else max(0, target - have)
        chosen = ordered[:room]

        cursor_time = _schedule_tail(cur, moment)
        spacing = max(60, int(config.min_publish_interval_seconds()))
        planned = 0
        for item in chosen:
            cursor_time = cursor_time + timedelta(seconds=spacing)
            if _insert(cur, item, cycle, cursor_time, moment):
                planned += 1
        conn.commit()

        return {
            "outcome": PLANNED if planned else QUEUE_SATISFIED,
            "planned": planned,
            "depth": have + planned,
            "cycle": cycle,
            "eligible": len(eligible),
            "evaluated": len(candidates),
            "rejected": rejected,
            "horizon_until": _iso(cursor_time),
        }
    except Exception:
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
        log.warning("pulsedrop_plan_failed", exc_info=True)
        return {"outcome": "ERROR", "planned": 0, "depth": 0, "cycle": 0}
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def reconcile(*, now: datetime | None = None, system_user_ids=()) -> dict:
    """Enroll catalog products that have no campaign in this generation.

    How new listings reach the schedule. It is :func:`plan` with ``force``, and
    exists as its own name because the two callers mean different things and the
    run log should be able to say which happened.

    Why this is a pull and not a push
    ---------------------------------
    The obvious alternative is for the marketplace to notify PulseDrop when a
    listing is published, and it was rejected on four counts.

    It would invert §2. The marketplace is the canonical product authority and
    PulseDrop is a curator of it; a curator that has to be told is a curator the
    authority now depends on. Publishing a listing would acquire a reason to
    care whether the content engine is healthy.

    There is no one place to hook. A listing reaches a published state from at
    least six write sites -- the dropship drafts publisher, admin review, and
    four seller paths including re-submit after a pause -- and one of those
    already bypasses the drafts validator. An enrollment hook would have to be
    added to each and kept on each, and the failure mode of missing one is
    invisible: products silently never enrolled.

    It would put this planner inside a seller's request. A hook fires in the
    publish transaction, so a slow candidate query becomes slow listing
    creation, and a planner exception becomes a failed publish.

    And it is not needed, because the read is already cheap.
    :func:`eligibility.fetch_candidates` is ordered newest-first and capped by
    :func:`config.candidate_limit`, so this is a bounded query and not a catalog
    scan -- §57 is satisfied by the shape of the existing read rather than by
    adding a delta column and an index to ``marketplace_listings``. It is also
    strictly cheaper than what production does today: the opportunistic curator
    runs this same read on *every* tick in order to decide afresh each time.

    The honest cost of pulling is latency -- a product is enrolled on the next
    tick rather than the instant it is published. For an account that publishes
    on an hourly floor, that is not a cost at all.

    Self-healing falls out of it
    ----------------------------
    Because this asks the catalog rather than trusting a notification, it also
    repairs. A product missed during an outage, one whose enrollment hook would
    have failed, or one released earlier and now back in stock is picked up by
    the next pass without anything needing to have recorded that it was missed.
    An event-driven design would need a reconciliation pass anyway for exactly
    this, which makes the events the redundant half rather than this.
    """
    return plan(now=now, system_user_ids=system_user_ids, force=True)


def _listings_in_cycle(cur, cycle: int) -> set[int]:
    """Listing ids already spoken for in this cycle.

    One query, not one per candidate: the planner runs against a few hundred
    candidates and the brief forbids an N+1 here by name.
    """
    cur.execute(
        "SELECT listing_id FROM pulsedrop_campaigns WHERE cycle = ? AND state <> ?",
        (int(cycle), RELEASED),
    )
    return {int(row[0] or 0) for row in cur.fetchall() or []}


def _schedule_tail(cur, now: datetime) -> datetime:
    """The time the last already-planned campaign goes out.

    New campaigns are appended after it so the spacing between consecutive
    publications holds across planning runs. Without this each top-up would
    start counting from ``now`` and stack several campaigns onto the same
    minute — the queue would be deep and the account would still publish in
    bursts, which is the failure this spacing exists to prevent.
    """
    cur.execute(
        f"SELECT MAX(scheduled_for) FROM pulsedrop_campaigns WHERE state IN ({_q(OPEN_STATES)})",
        tuple(OPEN_STATES),
    )
    raw = _first(cur.fetchone())
    if not raw:
        return now
    try:
        tail = datetime.fromisoformat(str(raw))
    except (TypeError, ValueError):
        return now
    # A tail in the past means the queue has drained while the loop was idle;
    # resuming from `now` avoids planning a hundred campaigns that are all
    # instantly overdue and would publish back-to-back on the next few ticks.
    return tail if tail > now else now


def _declustered(ranked) -> list:
    """Rank order, rearranged so one seller or category cannot run in a block.

    Ranking is about quality and says nothing about variety, so the top of a
    strict rank order is routinely six products from whichever seller photographs
    best. Spacing them here, at plan time, is cheaper and far more effective than
    discovering it at publish time: a cooldown can only ever reject the candidate
    in front of it, whereas the schedule can simply interleave.

    This is a deliberately simple round-robin over ``(seller, category)``
    buckets, preserving rank within each. It is worth being clear that in the
    loop this *is* the seller-fairness mechanism, not a cosmetic pass in front
    of one: ``execute_due`` does not consult ``diversity``'s per-candidate
    rules, for the reasons its own docstring gives, so there is no second
    enforcement behind this. The round-robin is what stops one merchant holding
    a run of slots.

    That is a fair trade rather than a loss. ``seller_share_blocked`` could only
    ever veto the candidate in front of it, and on a single-seller catalog —
    production's actual state — it is written to not bite at all. Interleaving
    at plan time cannot be defeated by the order candidates happen to arrive in.
    """
    buckets: dict[tuple, list] = {}
    for item in ranked:
        listing = item.listing
        key = (int(listing.get("seller_user_id") or 0), str(listing.get("category") or ""))
        buckets.setdefault(key, []).append(item)

    # Rank position of each bucket's best candidate, so that buckets of equal
    # depth are still drained in quality order.
    rank_of = {key: i for i, key in enumerate(buckets)}

    out: list = []
    previous: tuple | None = None
    while buckets:
        # Deepest bucket first, and never the bucket just emitted from while any
        # other still holds something.
        #
        # A plain round-robin over the keys looks like it does this and does not.
        # It ends each pass on whichever bucket sits last in rank order, then
        # opens the next pass on the only bucket with anything left -- which, if
        # that is the same bucket, puts a seller's two listings side by side.
        # Four products in three buckets is enough to show it, which is to say
        # the shape of a real small catalog rather than a contrived one.
        #
        # Draining the deepest bucket first is also what makes the gaps *even*
        # rather than merely non-zero. A seller holding six of ten slots has to
        # take roughly every other slot; a rank-ordered pass would schedule the
        # four other products first and leave that seller a block of six at the
        # end, which is a worse schedule than the one we started with.
        choices = sorted(buckets, key=lambda k: (-len(buckets[k]), rank_of[k]))
        key = next((k for k in choices if k != previous), choices[0])
        out.append(buckets[key].pop(0))
        previous = key
        if not buckets[key]:
            del buckets[key]
    return out


def _insert(cur, item, cycle: int, scheduled_for: datetime, now: datetime) -> bool:
    """Write one campaign. Returns whether it was actually created.

    ``campaign_key`` is UNIQUE and is the same shape of guard the publisher uses
    for publications: the insert *is* the claim, so two instances planning at
    once cannot both create a campaign for the same product in the same cycle,
    and a planner that is retried after a partial commit collides with its own
    earlier rows instead of doubling the horizon.

    The return value reads ``rowcount``, which is worth a note because the
    publisher deliberately does not — it re-reads its row instead, on the
    belief that a suppressed ``ON CONFLICT DO NOTHING`` reports its count
    differently on the two engines. Measured, it does not: 1 then 0 on SQLite
    via ``sqlite3``, and 1 then 0 on Postgres through ``db.CompatConnection``.
    (``RETURNING id`` also behaves identically — a row, then ``None``.) So the
    extra round trip is not buying anything here.

    The distinction matters more than the round trip. A read-back cannot tell
    "I created this" from "this already existed", because both leave a row with
    that key; using one would make every collision count as a plan, and
    ``planned`` is the number the run log uses to decide whether replenishment
    is working.

    Why the conflict arm updates instead of doing nothing
    -----------------------------------------------------
    Because ``DO NOTHING`` made two deliberate decisions contradict each other.
    :func:`_listings_in_cycle` excludes :data:`RELEASED` rows so that a product
    the catalogue withdrew mid-flight returns to the pool — it was never
    published, so holding it out of the whole generation would punish exactly
    the products most likely to come back into stock. But the unique key is
    ``(cycle, listing)``, so the released row *is* still occupying that key, and
    a suppressed insert meant the planner picked the product, believed it had
    scheduled it, and scheduled nothing. With a one-product catalogue that is a
    loop that silently stops.

    Reviving the released row is the resolution that keeps the invariant exactly
    true rather than weakening it. "One campaign per product per generation"
    stays literal: a released campaign is that generation's campaign, not yet
    delivered, so rescheduling it is the same campaign getting another date —
    not a second one. Adding a release counter to the key would have been the
    alternative, and it would have made the key stop meaning what its name says.

    The ``WHERE`` is what keeps this safe, and it is doing the load-bearing
    work: only a released row can be revived. A conflict with a ``scheduled``,
    ``publishing``, ``published``, ``partial`` or ``failed`` row still changes
    nothing and still reports zero, so the idempotency guarantee of §40 is
    untouched — a retried planner cannot reopen a campaign that has already
    reached members, and two instances planning at once still cannot both claim
    a product.
    """
    listing = item.listing
    listing_id = int(listing.get("id") or 0)
    if not listing_id:
        return False
    key = campaign_key(cycle, listing_id)
    stamp = _iso(now)
    score = float(getattr(item.score, "total", 0.0) or 0.0)
    try:
        cur.execute(
            "INSERT INTO pulsedrop_campaigns "
            "(campaign_key, cycle, listing_id, seller_user_id, category, state, "
            " scheduled_for, rank_score, want_signal, want_reel, max_attempts, "
            " planned_at, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (campaign_key) DO UPDATE SET "
            "  state = ?, scheduled_for = ?, rank_score = ?, attempts = 0, "
            "  claimed_by = '', claimed_at = NULL, released_reason = '', "
            "  failure_reason = '', planned_at = ?, updated_at = ? "
            "WHERE pulsedrop_campaigns.state = ?",
            (
                key, int(cycle), listing_id,
                int(listing.get("seller_user_id") or 0),
                str(listing.get("category") or "")[:120],
                SCHEDULED, _iso(scheduled_for), score,
                1, 1, int(config.loop_max_attempts()),
                stamp, stamp, stamp,
                # The revival arm.
                SCHEDULED, _iso(scheduled_for), score, stamp, stamp, RELEASED,
            ),
        )
    except Exception:
        # One malformed listing must not abort the whole replenishment (§44),
        # so this is swallowed per row rather than raised to `plan`.
        log.warning("pulsedrop_campaign_insert_failed key=%s", key, exc_info=True)
        return False
    return int(cur.rowcount or 0) > 0


def campaign_key(cycle: int, listing_id: int) -> str:
    """The idempotency key. One campaign per product per generation.

    Deliberately *not* date-based, unlike ``pulsedrop_publications``. A
    publication's natural identity includes the day it happened because the same
    product may legitimately be published again months later; a campaign's
    identity is its position in a generation, and a planner retried an hour
    later must collide with its own row rather than create a second one under a
    new date.
    """
    return f"c{int(cycle)}:{int(listing_id)}"


def _maybe_roll_cycle(cur, conn, cycle: int, eligible, now: datetime) -> int | None:
    """Open the next generation, or decline to.

    Returns the new cycle number, or ``None`` when the cycle must stay closed —
    which is the :data:`SUPPLY_LIMITED` case.

    The gate is the existing per-product cooldown, reused rather than reinvented.
    ``PULSEDROP_PRODUCT_COOLDOWN_HOURS`` already encodes the one judgement that
    matters here: how long before this account may show the same product again.
    A separate "cycle gap" knob would be a second answer to that question, and
    the two would eventually disagree.

    So: the next cycle opens when the *most recent* campaign in this one is
    further in the past than that cooldown. Most recent, not oldest -- the
    oldest tells you when the generation began, and rolling on it would let the
    product published an hour ago be re-planned immediately.
    """
    hours = int(config.product_cooldown_hours())
    if hours <= 0:
        # An operator has explicitly removed the per-product cooldown. Cycles
        # then have nothing to space them and roll freely; that is what setting
        # it to zero asks for.
        return _bump_cycle(cur, conn, cycle)
    cur.execute(
        "SELECT MAX(COALESCE(published_at, scheduled_for)) FROM pulsedrop_campaigns "
        "WHERE cycle = ? AND state <> ?",
        (int(cycle), RELEASED),
    )
    raw = _first(cur.fetchone())
    if not raw:
        return _bump_cycle(cur, conn, cycle)
    try:
        latest = datetime.fromisoformat(str(raw))
    except (TypeError, ValueError):
        return None
    if now - latest < timedelta(hours=hours):
        return None
    return _bump_cycle(cur, conn, cycle)


def _bump_cycle(cur, conn, cycle: int) -> int:
    nxt = int(cycle) + 1
    log.info("pulsedrop_cycle_opened cycle=%s", nxt)
    return nxt


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------

#: Nothing was due. The overwhelmingly common answer and not worth a run row.
NONE_DUE = "none_due"
#: Another instance took the campaign between our read and our claim.
LOST_CLAIM = "lost_claim"
#: Due, but its Reel is still encoding. Rescheduled, not failed.
AWAITING_RENDER = "awaiting_render"
#: Due, and the account is inside a cap or a pacing floor. Rescheduled.
PACED = "paced"


def execute_due(*, now: datetime | None = None, system_user_ids=()) -> dict:
    """Publish the campaign that is due, if one is. At most one per call.

    Must be called inside the curator's lease. This function takes a row-level
    claim of its own as well, which is not redundant: the lease stops two
    *ticks* overlapping, and the claim stops a campaign being published twice if
    a lease is ever lost to a clock skew or a long GC pause. Belt and braces is
    the right posture for a function whose failure mode is a duplicate post on a
    seller's product.

    ## Which of ``diversity``'s rules apply here, and why not all of them

    This is the load-bearing decision in the module, so it is written down
    rather than left to be inferred from which calls are absent.

    ``diversity.History`` answers two different kinds of question. One is about
    the *account*: has anything published in the last ninety minutes, has the
    rolling day's cap been used up. Those are consulted, because they are live
    facts that a schedule written a fortnight ago cannot know, and because they
    are the circuit breaker — if a reaper misfires or a queue is replanned, the
    cap is the thing standing between that and a burst on a real feed.

    The other is about the *candidate*: when was this product last published,
    this seller, this category, this product on the other surface. Those are
    **not** consulted, and that is the point of the cycle model rather than an
    oversight. A schedule that re-asked them would deadlock on its own
    decisions: ``campaign_key`` already guarantees one campaign per product per
    generation, ``_maybe_roll_cycle`` already gates the next generation on
    ``product_cooldown_hours``, and ``_declustered`` already spaces sellers and
    categories at plan time. Asking a 336-hour product cooldown to also approve
    a row the planner wrote *because* the product was due would release
    campaign after campaign and drain the horizon.

    That is not hypothetical. It is what production does today: every recent
    publication is Reel-only with reason ``signal_unavailable``, because the
    Signals all went out in one burst and then sat behind the fourteen-day
    product cooldown while the Reel cooldowns had been set to zero. The two
    surfaces had no shared unit of work, so nothing could notice they had come
    apart. Here the pair is one row and the cooldowns key on the generation, so
    the drift has nowhere to happen.

    The surface sub-caps (``daily_reel_cap``, ``reel_min_interval_seconds``) go
    with the per-candidate rules for the same reason: when every campaign is a
    pair, a Reel ceiling below the publication ceiling can only be satisfied by
    breaking pairs. ``health`` reports that conflict rather than resolving it
    silently.
    """
    from services import db as db_service
    from services.pulsedrop import diversity, publisher, ranking, reel_composer

    moment = _now(now)
    if not config.enabled() or not config.loop_enabled():
        return {"outcome": DISABLED}

    schema.ensure_schema()
    conn = None
    campaign = {}
    try:
        conn = db_service.connect()
        cur = conn.cursor()
        campaign = next_due(cur, moment)
        if not campaign:
            return {"outcome": NONE_DUE}
        if not _claim(cur, conn, campaign, moment):
            return {"outcome": LOST_CLAIM, "campaign_id": int(campaign.get("id") or 0)}

        listing_id = int(campaign.get("listing_id") or 0)
        fresh, blocker = eligibility.revalidate(
            cur, listing_id, system_user_ids=system_user_ids, now=moment
        )
        # Loaded on this connection while it is still open, so the pacing
        # decision costs no second trip to the pool. See the docstring for
        # which of its questions are asked.
        history = diversity.History.load(cur, now=moment)
    finally:
        # The read connection closes before anything publishes, for the reason
        # the curator's own docstring gives: publication is three writers and
        # holding a pool slot across all of them is how this repo has emptied
        # the pool before.
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass

    campaign_id = int(campaign.get("id") or 0)
    if blocker:
        # The catalog withdrew it. Not our failure, and emphatically not a
        # publication: release the slot and let the planner refill it.
        _release(campaign_id, blocker, now=moment)
        return {
            "outcome": RELEASED, "campaign_id": campaign_id,
            "listing_id": listing_id, "reason": blocker,
        }

    attempts_now = int(campaign.get("attempts") or 0)
    paced = ""
    if history.daily_cap_reached():
        paced = "daily_cap"
    elif history.min_interval_blocked():
        paced = "min_interval"
    if paced:
        # Deferred without consuming an attempt. Attempts exist to retire a
        # campaign that cannot work; this one is fine and the account is simply
        # busy, so letting the cap spend its three tries would park a healthy
        # pair as failed for the crime of being scheduled on a full day.
        _defer(campaign_id, moment, attempts_now,
               seconds=max(60, int(config.min_publish_interval_seconds())))
        return {
            "outcome": PACED, "campaign_id": campaign_id,
            "listing_id": listing_id, "reason": paced,
        }

    ranked_list = ranking.rank([fresh], now=moment)
    if not ranked_list:
        _release(campaign_id, "unrankable", now=moment)
        return {"outcome": RELEASED, "campaign_id": campaign_id, "reason": "unrankable"}
    ranked = ranked_list[0]

    # What the campaign promised, narrowed only by the global kill switches --
    # and kept, because the final state is judged against this rather than
    # against whatever the campaign ends up attempting.
    #
    # The distinction is the difference between an honest run log and a useless
    # one. A surface an operator has switched off was never promised, so a
    # Reel-only publication under `signals_enabled() == false` is exactly what
    # was asked for. A surface this campaign *gave up on* was promised, and
    # judging against the narrowed intent would make every abandoned half
    # recompute its own success: one wanted, one landed, PUBLISHED. Pairs would
    # then stop being pairs with nothing anywhere recording that they had --
    # which is the production failure this whole module exists to fix, rebuilt
    # inside the thing meant to prevent it.
    promised_signal = bool(int(campaign.get("want_signal") or 0)) and config.signals_enabled()
    promised_reel = bool(int(campaign.get("want_reel") or 0)) and config.reels_enabled()
    want_signal = promised_signal
    want_reel = promised_reel
    attempts = attempts_now
    max_attempts = max(1, int(campaign.get("max_attempts") or 3))

    render = {}
    reasons: list[str] = []
    if want_reel:
        from services.pulsedrop import distribution

        source = distribution.reel_source_for(fresh)
        if not source:
            # This product cannot make a Reel at all -- no video, too few
            # stills. Not a reason to withhold the Signal, and not a reason to
            # keep retrying: drop the Reel half for this campaign and say so.
            #
            # "Say so" is the reason this records a reason. The planner only
            # schedules products the eligibility gate said could carry both
            # surfaces, so arriving here means the media changed underneath a
            # scheduled campaign. That is worth seeing in the run log, not
            # worth failing over.
            want_reel = False
            reasons.append("reel_source_unavailable")
        else:
            render = reel_composer.find_or_enqueue(fresh, source, now=moment)
            state = str(render.get("state") or "")
            if state != reel_composer.READY or not render.get("video_url"):
                if attempts + 1 < max_attempts:
                    # Hold the pair together. Pushing the whole campaign out by
                    # one render window is the single most important difference
                    # between Pulse Loop and what production does today, where
                    # the Signal went out immediately and its Reel arrived days
                    # later -- or never.
                    _defer(campaign_id, moment, attempts + 1,
                           seconds=int(config.reel_render_timeout_seconds()))
                    return {
                        "outcome": AWAITING_RENDER, "campaign_id": campaign_id,
                        "listing_id": listing_id, "render_state": state,
                        "attempts": attempts + 1,
                    }
                # Out of patience. Ship the half that works rather than letting
                # one unencodable product occupy a slot forever -- but record
                # that the pair was broken, which is what PARTIAL is for.
                want_reel = False
                render = {}
                reasons.append(f"reel_render_{state or 'timeout'}")

    signal_post_id = 0
    reel_post_id = 0

    if want_reel and render:
        result = publisher.publish_reel(ranked, render, now=moment)
        if result.ok:
            reel_post_id = int(result.post_id or 0)
        else:
            reasons.append(f"reel_{result.reason}")

    if want_signal:
        result = publisher.publish_signal(ranked, now=moment)
        if result.ok:
            signal_post_id = int(result.post_id or 0)
        else:
            reasons.append(f"signal_{result.reason}")

    promised = int(bool(promised_signal)) + int(bool(promised_reel))
    landed = int(bool(signal_post_id)) + int(bool(reel_post_id))
    if landed == 0:
        state = FAILED
    elif landed < promised:
        state = PARTIAL
    else:
        state = PUBLISHED

    _settle(
        campaign_id, state, now=moment,
        signal_post_id=signal_post_id, reel_post_id=reel_post_id,
        render_id=int(render.get("id") or 0) if render else 0,
        reason=",".join(reasons)[:200],
    )
    return {
        "outcome": state, "campaign_id": campaign_id, "listing_id": listing_id,
        "signal_post_id": signal_post_id, "reel_post_id": reel_post_id,
        "cycle": int(campaign.get("cycle") or 0),
        "reason": ",".join(reasons)[:200],
    }


def _claim(cur, conn, campaign: dict, now: datetime) -> bool:
    """Move one campaign from scheduled to publishing, or lose the race.

    The ``AND state = 'scheduled'`` is the entire mechanism: it is a conditional
    update, so exactly one of two concurrent instances can match, and the loser
    sees ``rowcount == 0`` rather than an exception. Unlike the publications
    claim this can rely on ``rowcount`` -- an UPDATE's affected-row count is
    unambiguous on both drivers, whereas the suppressed-INSERT count that forced
    the publisher to read its claim back is not.
    """
    try:
        cur.execute(
            "UPDATE pulsedrop_campaigns SET state = ?, claimed_by = ?, claimed_at = ?, "
            "updated_at = ? WHERE id = ? AND state = ?",
            (PUBLISHING, _owner(), _iso(now), _iso(now),
             int(campaign.get("id") or 0), SCHEDULED),
        )
        taken = int(cur.rowcount or 0) > 0
        conn.commit()
        return taken
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        log.warning("pulsedrop_campaign_claim_failed id=%s",
                    campaign.get("id"), exc_info=True)
        return False


def _owner() -> str:
    """Who holds a claim. Host plus pid, same shape the lease module uses."""
    import os
    import socket

    try:
        return f"{socket.gethostname()}:{os.getpid()}"[:120]
    except Exception:
        return f"pid:{os.getpid()}"


def _update(campaign_id: int, assignments: dict, *, where_state: str | None = None) -> bool:
    """One small committed write against one campaign row.

    Its own connection on purpose. Every caller is on the publish side, after
    the read connection has been returned to the pool, and each of these is the
    last word on a campaign whose posts may already exist -- so it must not be
    batched into a transaction that something else can roll back.
    """
    from services import db as db_service

    if not campaign_id or not assignments:
        return False
    columns = list(assignments.keys())
    sql = (f"UPDATE pulsedrop_campaigns SET {', '.join(f'{c} = ?' for c in columns)} "
           "WHERE id = ?")
    params = [assignments[c] for c in columns] + [int(campaign_id)]
    if where_state is not None:
        sql += " AND state = ?"
        params.append(where_state)
    conn = None
    try:
        conn = db_service.connect()
        cur = conn.cursor()
        cur.execute(sql, tuple(params))
        changed = int(cur.rowcount or 0) > 0
        conn.commit()
        return changed
    except Exception:
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
        log.warning("pulsedrop_campaign_update_failed id=%s", campaign_id, exc_info=True)
        return False
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def _settle(campaign_id: int, state: str, *, now: datetime, signal_post_id: int = 0,
            reel_post_id: int = 0, render_id: int = 0, reason: str = "") -> None:
    stamp = _iso(now)
    _update(campaign_id, {
        "state": state,
        "signal_post_id": int(signal_post_id),
        "reel_post_id": int(reel_post_id),
        "render_id": int(render_id),
        "failure_reason": str(reason or "")[:200],
        "claimed_by": "",
        "published_at": stamp if state in (PUBLISHED, PARTIAL) else None,
        "updated_at": stamp,
    })
    log.info(
        "pulsedrop_campaign_settled id=%s state=%s signal=%s reel=%s reason=%s",
        campaign_id, state, signal_post_id, reel_post_id, reason,
    )


def _release(campaign_id: int, reason: str, *, now: datetime) -> None:
    stamp = _iso(now)
    _update(campaign_id, {
        "state": RELEASED,
        "released_reason": str(reason or "")[:120],
        "claimed_by": "",
        "updated_at": stamp,
    })
    log.info("pulsedrop_campaign_released id=%s reason=%s", campaign_id, reason)


def _defer(campaign_id: int, now: datetime, attempts: int, *, seconds: int) -> None:
    """Put a claimed campaign back in the queue, slightly later.

    Returns it to ``scheduled`` rather than leaving it ``publishing``, so the
    stale-claim reaper does not later count a campaign that is politely waiting
    for an encoder as a crashed instance.
    """
    stamp = _iso(now)
    _update(campaign_id, {
        "state": SCHEDULED,
        "scheduled_for": _iso(now + timedelta(seconds=max(60, int(seconds)))),
        "attempts": int(attempts),
        "claimed_by": "",
        "updated_at": stamp,
    })


# ---------------------------------------------------------------------------
# Preparation and self-healing
# ---------------------------------------------------------------------------


def prewarm(*, now: datetime | None = None, limit: int = 3) -> dict:
    """Enqueue Reel encodes for campaigns that are about to come due.

    The reason Pulse Loop can promise a pair at all. An encode takes minutes;
    a tick has a five-minute lease and a campaign has an exact due time. Asking
    for the render *before* the due time turns "the Signal publishes now and the
    Reel follows whenever" into "both publish together", because by the time the
    campaign is due the file already exists.

    This is deliberately only an enqueue. Draining the queue stays with
    ``reel_composer.run_pending`` outside the lease, where a ten-minute ffmpeg
    run is nobody's problem.

    Idempotent by construction: ``find_or_enqueue`` is keyed on the product, its
    source media and the composition version, so calling this every cycle for
    the same campaign returns the same render instead of queueing a second one.
    """
    from services import db as db_service
    from services.pulsedrop import distribution, reel_composer

    moment = _now(now)
    out = {"examined": 0, "enqueued": 0, "ready": 0}
    if not config.enabled() or not config.loop_enabled() or not config.reels_enabled():
        return out

    lead = max(60, int(config.loop_render_lead_seconds()))
    horizon = _iso(moment + timedelta(seconds=lead))
    schema.ensure_schema()
    conn = None
    rows: list[dict] = []
    try:
        conn = db_service.connect()
        cur = conn.cursor()
        cur.execute(
            "SELECT * FROM pulsedrop_campaigns WHERE state = ? AND want_reel = 1 "
            "AND scheduled_for <= ? ORDER BY scheduled_for ASC LIMIT ?",
            (SCHEDULED, horizon, max(1, int(limit))),
        )
        candidates = [dict(row) for row in cur.fetchall() or []]
        for item in candidates:
            listing, blocker = eligibility.revalidate(
                cur, int(item.get("listing_id") or 0), now=moment
            )
            # A product that has gone away is not released here. Prewarming is
            # an optimisation and must not make editorial decisions -- if it
            # released the campaign, a transient catalog read would silently
            # drop a scheduled pair. `execute_due` is the only place allowed to
            # conclude that, and it will reach the same answer a minute later.
            if not blocker and listing:
                rows.append(listing)
    except Exception:
        log.warning("pulsedrop_prewarm_read_failed", exc_info=True)
        return out
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass

    for listing in rows:
        out["examined"] += 1
        try:
            source = distribution.reel_source_for(listing)
            if not source:
                continue
            render = reel_composer.find_or_enqueue(listing, source, now=moment)
            state = str(render.get("state") or "")
            if state == reel_composer.READY and render.get("video_url"):
                out["ready"] += 1
            elif state == reel_composer.PENDING:
                out["enqueued"] += 1
        except Exception:
            # One unencodable product must not stop the others being prepared.
            log.warning("pulsedrop_prewarm_item_failed listing=%s",
                        listing.get("id"), exc_info=True)
    return out


def reap_stale_claims(*, now: datetime | None = None) -> int:
    """Return crashed in-flight campaigns to the queue.

    A campaign left ``publishing`` by an instance that died is the campaign
    equivalent of the publications reaper's stuck claim, and it is worse: the
    slot is counted as occupied, so the planner will not refill it, and the
    campaign will never come due again because nothing looks at ``publishing``
    rows. The horizon would quietly leak a slot per crash.

    Reclaiming to ``scheduled`` rather than to ``failed`` is safe because
    ``publish_signal`` and ``publish_reel`` are themselves idempotent per
    surface per product per day -- a republished campaign collides with its own
    publication claim rather than posting twice.

    The staleness decision is made in Python rather than in the ``WHERE``
    clause, which is worth a sentence because the SQL version is shorter and
    wrong. ``claimed_at`` is a TEXT column, so a comparison against a cutoff
    string is lexicographic, and ``' '`` sorts below ``'T'`` -- a timestamp
    written ``2026-10-01 12:00:00`` therefore reads as older than *every*
    cutoff of the same day written ``2026-10-01T...``. A single writer using the
    other separator would make the reaper treat live claims as crashed and
    republish campaigns out from under the instance still working on them. This
    module only ever writes :func:`_iso`, so today the two forms agree; relying
    on that is relying on nobody ever touching the column from elsewhere, which
    in a schema with no migration framework is not a safe thing to rely on.

    Parsing is also what makes an unreadable timestamp fail in the safe
    direction: a row whose ``claimed_at`` cannot be parsed at all is treated as
    stale and reclaimed, because the alternative is leaking its slot forever.
    """
    from services import db as db_service

    moment = _now(now)
    horizon = timedelta(seconds=max(60, int(config.lease_seconds()) * 2))
    schema.ensure_schema()
    conn = None
    try:
        conn = db_service.connect()
        cur = conn.cursor()
        # Always a handful of rows -- one per instance publishing right now --
        # so reading them to decide is not a scan.
        cur.execute(
            "SELECT id, claimed_at FROM pulsedrop_campaigns WHERE state = ?",
            (PUBLISHING,),
        )
        stale = [
            int(row[0] or 0)
            for row in cur.fetchall() or []
            if _is_stale(row[1], moment, horizon)
        ]
        count = 0
        if stale:
            marks = ", ".join("?" for _ in stale)
            cur.execute(
                f"UPDATE pulsedrop_campaigns SET state = ?, claimed_by = '', "
                f"updated_at = ? WHERE id IN ({marks})",
                (SCHEDULED, _iso(moment), *stale),
            )
            count = int(cur.rowcount or 0)
        conn.commit()
        if count:
            log.info("pulsedrop_campaigns_reaped count=%s", count)
        return max(0, count)
    except Exception:
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
        log.warning("pulsedrop_campaign_reap_failed", exc_info=True)
        return 0
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Observability
# ---------------------------------------------------------------------------


def health(*, now: datetime | None = None) -> dict:
    """One dict answering "is the loop loaded, moving, and honest about why not".

    Built for the admin page and for an alert, and deliberately cheap: counts
    and extremes over an indexed table, no catalog read. That is also why it
    cannot tell you whether supply is limited — answering that means ranking
    the catalog, which is :func:`plan`'s job and is recorded as its outcome.

    Which matters, because ``below_min`` is the field someone will reach for as
    an alert and it is the wrong one on its own. A shallow queue because the
    catalog is exhausted is a business fact no engineer can fix; a shallow queue
    because planning is throwing is an incident. Both set ``below_min``. The
    pair to page on is ``below_min`` **and** a planner outcome that is neither
    ``CATALOG_SUPPLY_LIMITED`` nor ``QUEUE_SATISFIED``.
    """
    from services import db as db_service

    moment = _now(now)
    schema.ensure_schema()
    conn = None
    try:
        conn = db_service.connect()
        cur = conn.cursor()
        cycle = current_cycle(cur)
        open_depth = depth(cur)
        cur.execute(
            "SELECT state, COUNT(*) FROM pulsedrop_campaigns GROUP BY state"
        )
        by_state = {str(row[0] or ""): int(row[1] or 0) for row in cur.fetchall() or []}
        cur.execute(
            f"SELECT MIN(scheduled_for) FROM pulsedrop_campaigns WHERE state IN ({_q(OPEN_STATES)})",
            tuple(OPEN_STATES),
        )
        next_at = _first(cur.fetchone())
        cur.execute(
            f"SELECT MAX(scheduled_for) FROM pulsedrop_campaigns WHERE state IN ({_q(OPEN_STATES)})",
            tuple(OPEN_STATES),
        )
        last_at = _first(cur.fetchone())
        cur.execute(
            "SELECT COUNT(*) FROM pulsedrop_campaigns WHERE state = ? AND scheduled_for <= ?",
            (SCHEDULED, _iso(moment)),
        )
        overdue = int(_first(cur.fetchone()) or 0)
        coming = upcoming(cur, limit=10)
    except Exception:
        log.warning("pulsedrop_campaign_health_failed", exc_info=True)
        return {"readable": False}
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass

    target = config.loop_target_depth()
    floor = min(config.loop_min_depth(), target)
    return {
        "readable": True,
        "enabled": bool(config.enabled() and config.loop_enabled()),
        "cycle": cycle,
        "depth": open_depth,
        "target_depth": target,
        "min_depth": floor,
        "below_min": open_depth <= floor,
        "overdue": overdue,
        "next_scheduled_for": next_at or "",
        "horizon_until": last_at or "",
        "by_state": by_state,
        "upcoming": coming,
        "warnings": _config_warnings(),
    }


def seconds_until_next_due(*, now: datetime | None = None) -> int | None:
    """Whole seconds until the earliest open campaign is due, or ``None``.

    ``None`` means there is nothing scheduled, or the table could not be read —
    both of which the caller should answer by falling back to its own interval
    rather than by picking a number. ``0`` means something is due now, which is
    a different and actionable answer, so the two are not conflated.

    This exists because the loop makes the curator's evaluation interval the
    wrong clock. That interval is a sampling rate for "go and look at the
    catalog", and two hours is a sensible rate for that. A campaign has an exact
    due time, and sampling every two hours for events ninety minutes apart
    publishes everything late by up to the difference. Here the schedule is
    readable, so the tick can wake when there is actually something to do.
    """
    from services import db as db_service

    moment = _now(now)
    conn = None
    try:
        conn = db_service.connect()
        cur = conn.cursor()
        cur.execute(
            f"SELECT MIN(scheduled_for) FROM pulsedrop_campaigns "
            f"WHERE state IN ({_q(OPEN_STATES)})",
            tuple(OPEN_STATES),
        )
        raw = _first(cur.fetchone())
    except Exception:
        log.debug("pulsedrop_next_due_unreadable", exc_info=True)
        return None
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
    if not raw:
        return None
    try:
        when = datetime.fromisoformat(str(raw))
    except (TypeError, ValueError):
        return None
    return max(0, int((when - moment).total_seconds()))


def _config_warnings() -> list[str]:
    """Settings that are individually valid and contradict each other.

    These are not validation failures — each value is inside its own clamp, and
    each was a reasonable thing to type. They are pairs whose *relationship*
    matters, which no single-field clamp can police, and whose symptom when
    wrong is not an error but content that quietly comes out worse than the
    operator asked for. So they are reported on the page rather than enforced.
    """
    notes: list[str] = []

    lead = config.loop_render_lead_seconds()
    encode = config.reel_render_timeout_seconds()
    if lead <= encode:
        notes.append(
            f"Render lead ({lead}s) is not longer than the render timeout "
            f"({encode}s), so a campaign can come due while its own encode is "
            f"still legitimately running and the pair will separate."
        )

    reel_cap = config.daily_reel_cap()
    post_cap = config.daily_publication_cap()
    if reel_cap < post_cap:
        notes.append(
            f"Reels per day ({reel_cap}) is below publications per day "
            f"({post_cap}). Every campaign is a pair, so the loop ignores the "
            f"Reel sub-cap; the effective limit on both surfaces is {post_cap}."
        )

    spacing = config.min_publish_interval_seconds()
    if spacing <= 0:
        notes.append(
            "Minimum gap between posts is 0, so the planner has no spacing to "
            "schedule by and will stack the whole horizon on one timestamp."
        )

    return notes
