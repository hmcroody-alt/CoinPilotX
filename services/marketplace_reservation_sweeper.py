"""Stage 4 — the durable expiry sweep.

What this closes
----------------
A reservation is a promise with a deadline, and until now nothing in production
ever checked whether a deadline had passed. Stripe emits no event when a buyer
dismisses the Apple Pay or PaymentSheet UI, so the ``payment_intent.canceled``
branch added in Stage 6 only fires when Stripe itself cancels the intent — which
it does eventually, but not always, and not promptly. Every other path out of a
hold is an event that might never arrive: a crashed app, a dropped network, a
force-quit mid-checkout. The sweep is the path that does not depend on anyone
telling us anything. It is the only reason ``expires_at`` is worth storing.

What this deliberately is not
-----------------------------
It is not a second settlement implementation. Stages 5-6 collapsed six private
copies of "release the hold, then move the transaction to a terminal status"
into ``settle_failed_transactions``, and the wiring guard now walks every module
under ``services/`` to keep it that way. This module therefore contains no
``UPDATE marketplace_listings``, no ``UPDATE seller_transactions`` and no direct
``release_inventory_reservation`` call. It decides *which* reservations to act
on and *why*; the acting is delegated, unchanged, to the shared path.

It is also not a scheduler. There is no loop here, no sleep and no thread. The
entry point runs one bounded batch and returns a structured summary, which is
what lets the twenty-four Stage 16 cases drive it deterministically and what
lets the worker in the next stage decide its own cadence without this module
having an opinion about wall-clock time.

The safety asymmetry, restated
------------------------------
Every ambiguous outcome defers. Deferring holds stock slightly too long;
releasing wrongly sells a paid item to a second buyer. A Stripe outage during a
sweep produces zero releases by construction, because
``decide_for_reservation`` turns any provider exception into ``defer`` — so one
provider incident cannot empty every hold in the store. That behaviour is
inherited rather than reimplemented here, which is the point.

Reading order for the decision path::

    run_reservation_expiry_sweep
      └─ select_expiry_candidates          bounded, index-backed, oldest first
         └─ for each row (isolated):
            └─ reconciler.decide_for_reservation
               ├─ local status settled?  → capture, no provider call
               ├─ no payment intent?     → release, no provider call
               └─ otherwise              → one Stripe read, then the table
            ├─ RELEASE → cart.settle_failed_transactions   (the shared path)
            ├─ CAPTURE → cart.capture_inventory_reservation (protective)
            └─ DEFER   → cart.note_reservation_deferral     (backoff + bound)
"""

from __future__ import annotations

import logging
import os
import time
from datetime import timedelta

from services import marketplace_cart_routes as cart
from services import marketplace_reservation_policy as reservation_policy
from services import marketplace_reservation_reconciler as reconciler
from services import marketplace_reservation_schema as reservation_schema

LOGGER = logging.getLogger(__name__)


# --------------------------------------------------------------------------
# Bounds
# --------------------------------------------------------------------------

#: Rows examined per call. The sweep costs at most one Stripe read per
#: candidate, so this is really a provider-traffic bound wearing a database
#: bound's clothing. Small enough that a backlog drains over several cycles
#: instead of arriving at Stripe as one burst; large enough that ordinary
#: abandonment never accumulates.
DEFAULT_BATCH_LIMIT = 50
BATCH_LIMIT_ENV_VAR = "MARKETPLACE_RESERVATION_SWEEP_BATCH"

#: How long a deferred reservation is left alone before it is looked at again.
#: Without this a row that Stripe reports as ``processing`` would be re-read on
#: every single cycle, so a worker running every thirty seconds would generate
#: a hundred and twenty provider calls an hour for one undecided order. The
#: deferral bound would still terminate it eventually, but only after paying
#: for the privilege.
DEFAULT_MIN_RECHECK_SECONDS = 5 * 60
MIN_RECHECK_ENV_VAR = "MARKETPLACE_RESERVATION_SWEEP_RECHECK_SECONDS"


def batch_limit() -> int:
    """Batch size, clamped. A typo must not turn one sweep into a full scan."""
    raw = (os.environ.get(BATCH_LIMIT_ENV_VAR) or "").strip()
    if not raw:
        return DEFAULT_BATCH_LIMIT
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_BATCH_LIMIT
    return max(1, min(value, 500))


def min_recheck_seconds() -> int:
    """Backoff window, clamped. Zero is disallowed — it would be a hot loop."""
    raw = (os.environ.get(MIN_RECHECK_ENV_VAR) or "").strip()
    if not raw:
        return DEFAULT_MIN_RECHECK_SECONDS
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_MIN_RECHECK_SECONDS
    return max(0, min(value, 60 * 60))


# --------------------------------------------------------------------------
# What a release becomes
# --------------------------------------------------------------------------
# The reservation's ``release_reason`` says why the hold ended; the
# transaction's status says what the order became. They are different
# vocabularies and mapping between them belongs in one place.
#
# ``canceled`` is reused verbatim from the ``payment_intent.canceled`` webhook
# branch so that an order cancelled by Stripe and the same order noticed by the
# sweep land in an identical state. Two names for one outcome would show up as
# two rows in every report an owner ever runs.

TERMINAL_STATUS_BY_REASON = {
    reservation_policy.REASON_PAYMENT_CANCELED: "canceled",
    reservation_policy.REASON_EXPIRED: "checkout_expired",
}
DEFAULT_TERMINAL_STATUS = "checkout_expired"


def terminal_status_for(release_reason: str | None) -> str:
    return TERMINAL_STATUS_BY_REASON.get(
        release_reason or "", DEFAULT_TERMINAL_STATUS)


# --------------------------------------------------------------------------
# Candidate selection
# --------------------------------------------------------------------------

#: Columns the decision needs, and nothing else. ``decide_for_reservation``
#: reads only ``stripe_payment_intent_id`` and the transaction status; the rest
#: is telemetry and bookkeeping.
#:
#: The reservation-side projection is assembled per call from the columns the
#: table actually has rather than written out as a constant. The previous
#: constant named ``reconciled_at`` and ``reconcile_deferrals`` unconditionally
#: and relied on a ``try/except`` fallback to cope with a database that lacked
#: them — but the fallback still named ``expires_at``, so on the database that
#: lacked *that*, both attempts raised the identical ``UndefinedColumn`` and the
#: sweep failed the way it was written not to. A projection that is built from
#: the observed shape cannot reference a column that is not there.
_CANDIDATE_TRANSACTION_COLUMNS = (
    "t.status AS transaction_status",
    "t.stripe_payment_intent_id AS stripe_payment_intent_id",
)


def _candidate_projection(present) -> list[str]:
    columns = [
        "r.seller_transaction_id AS seller_transaction_id",
        "r.listing_id AS listing_id",
        "r.quantity AS quantity",
        "r.expires_at AS expires_at",
    ]
    if "reconciled_at" in present:
        columns.append("r.reconciled_at AS reconciled_at")
    if "reconcile_deferrals" in present:
        columns.append("COALESCE(r.reconcile_deferrals, 0) AS reconcile_deferrals")
    columns.extend(_CANDIDATE_TRANSACTION_COLUMNS)
    return columns


def select_expiry_candidates(cur, *, now=None, limit: int | None = None,
                             recheck_seconds: int | None = None) -> list[dict]:
    """Expired, still-held, not-yet-settled reservations. Oldest first.

    The ``WHERE`` clause leads with ``r.status`` and ``r.expires_at`` so it can
    use ``idx_mkt_reservations_status_expires``. Without that index this query
    scans every reservation ever taken, which is fine for a month and painful
    for a year.

    Two filters do the real safety work. ``status='held'`` excludes anything
    already captured or released, so a re-run cannot touch a finished row. The
    join predicate excludes transactions that are locally ``paid`` or
    ``refunded``, so an order that settled is never even considered — the
    cheapest possible way to avoid asking Stripe about it and the cheapest
    possible way to avoid acting on it.

    That predicate is a filter, not a guarantee. A transaction can settle in the
    window between this query and the settlement call, which is why
    ``settle_failed_transactions`` carries its own ``NOT IN ('paid','refunded')``
    guard and why ``release_inventory_reservation`` is a compare-and-swap on
    ``status='held'``. Belt and braces, because the failure mode is selling a
    paid item twice.

    The deadline comparison is done twice on purpose. SQL compares ISO strings,
    which is index-friendly and correct whenever the timestamps share a format —
    they do, because every writer goes through ``reservation_policy``. Python
    then re-checks each row with ``is_expired``, which actually parses the
    timestamp and applies the grace window. The SQL narrows; the parse decides.
    A legacy row with an unparseable ``expires_at`` survives the string
    comparison and is then dropped by the parse, which is the correct outcome:
    inventing a deadline for a row that never had one could release stock for an
    order still in flight.
    """
    stamp = reservation_policy.parse_timestamp(now) or reservation_policy.now_utc()
    rows_limit = int(limit) if limit else batch_limit()
    rows_limit = max(1, min(rows_limit, 500))
    backoff = min_recheck_seconds() if recheck_seconds is None else int(recheck_seconds)

    # `expires_at <= now - grace` is exactly `now >= expires_at + grace`, which
    # is the predicate `is_expired` applies. Expressed this way so the constant
    # moves to the parameter and the column stays bare and indexable.
    grace = timedelta(seconds=reservation_policy.EXPIRY_GRACE_SECONDS)
    deadline_cutoff = (stamp - grace).isoformat(timespec="seconds")
    recheck_cutoff = (stamp - timedelta(seconds=backoff)).isoformat(timespec="seconds")

    settled = cart.SETTLED_TRANSACTION_STATUSES
    placeholders = ",".join("?" for _ in settled)

    # Read the real shape every call rather than trusting a process cache. The
    # cost is one introspection query per sweep — negligible against a five
    # minute cadence — and it buys the guarantee that the projection below can
    # never name a column this database does not have, including after a
    # concurrent migration changed the answer mid-process.
    present = reservation_schema.reservation_columns(cur, refresh=True)
    missing = reservation_schema.missing_sweep_columns(present)
    if missing:
        # Not a query failure and not an empty result. There is no honest answer
        # to "which holds have expired" on a table with no deadline column, and
        # returning zero candidates would be indistinguishable from a clean
        # sweep — which is precisely how a broken sweeper reads as a healthy one
        # on a dashboard.
        raise reservation_schema.ReservationSchemaMissing(missing)

    columns = ", ".join(_candidate_projection(present))
    params = [reservation_policy.STATUS_HELD, deadline_cutoff, *settled]

    recheck_clause = ""
    if "reconciled_at" in present:
        recheck_clause = (
            "  AND (r.reconciled_at IS NULL OR r.reconciled_at = '' "
            "       OR r.reconciled_at <= ?) ")
        params.append(recheck_cutoff)
    params.append(rows_limit)

    sql = (
        f"SELECT {columns} "
        "FROM marketplace_inventory_reservations r "
        "LEFT JOIN seller_transactions t ON t.id = r.seller_transaction_id "
        "WHERE r.status = ? "
        "  AND r.expires_at IS NOT NULL AND r.expires_at <> '' "
        "  AND r.expires_at <= ? "
        f"  AND (t.status IS NULL OR t.status NOT IN ({placeholders})) "
        f"{recheck_clause}"
        "ORDER BY r.expires_at ASC, r.seller_transaction_id ASC "
        "LIMIT ?"
    )

    cur.execute(sql, params)

    candidates = []
    for raw in cur.fetchall() or []:
        row = dict(raw)
        if not reservation_policy.is_expired(row.get("expires_at"), now=stamp):
            # Survived the string comparison but is not actually past due once
            # parsed — an unparseable legacy deadline, or a format this module
            # did not write. Left alone rather than guessed at.
            continue
        candidates.append(row)
    return candidates


# --------------------------------------------------------------------------
# The sweep
# --------------------------------------------------------------------------

#: Reasons a sweep produced no measurement. Kept as constants because they are
#: read by the worker heartbeat and by the acceptance report, and a typo in a
#: string literal would silently become a new category of failure nobody counts.
REASON_SCHEMA_MISSING = reservation_schema.REASON_SCHEMA_MISSING
REASON_SCHEMA_ENSURE_FAILED = reservation_schema.REASON_SCHEMA_ENSURE_FAILED
REASON_CANDIDATE_QUERY_FAILED = "candidate_query_failed"

STATUS_OK = "ok"
STATUS_DEGRADED = "degraded"


def _empty_result(*, dry_run: bool, limit: int) -> dict:
    return {
        "status": STATUS_OK, "reason": None,
        "scanned": 0, "candidates": 0,
        "released": 0, "captured": 0, "deferred": 0, "skipped": 0,
        "reconciled": 0, "failed": 0,
        "would_release": 0, "would_defer": 0, "would_skip": 0,
        "provider_calls": 0, "needs_attention": 0,
        "dry_run": bool(dry_run), "limit": int(limit),
        "batch_exhausted": False, "duration_ms": 0,
    }


def _degraded(result: dict, *, reason: str, started: float,
              detail: str | None = None, missing=()) -> dict:
    """Mark a sweep that could not look, as distinct from one that found nothing.

    ``failed=1`` and every other counter at zero is the same shape a broken
    sweep and a clean sweep both produce, which is why the reason travels with
    it. An operator reading ``candidates=0`` needs to know whether that is good
    news.
    """
    result["status"] = STATUS_DEGRADED
    result["reason"] = reason
    result["failed"] = 1
    if detail:
        result["error"] = detail[:500]
    if missing:
        result["schema_missing"] = list(missing)
    result["duration_ms"] = int((time.monotonic() - started) * 1000)
    return result


def run_reservation_expiry_sweep(cur, *, now=None, limit: int | None = None,
                                 dry_run: bool = False,
                                 fetch_status=None,
                                 recheck_seconds: int | None = None) -> dict:
    """Run one bounded sweep. Returns counts; raises only on catastrophe.

    ``cur`` is a cursor, matching every other function in this subsystem — the
    caller owns the connection, the transaction and the commit. That is what
    lets a test drive this against an in-memory SQLite database and lets the
    worker wrap it in the same ``bot.db()`` handling every other job uses.

    ``now`` and ``fetch_status`` are injected so the decision path is fully
    determinable without a clock or a network. ``dry_run`` evaluates every
    decision and writes nothing at all.

    The result is structured because the worker must not parse log lines to
    find out what happened. ``released`` counts holds actually returned;
    ``would_release`` counts decisions that said to, which are the same number
    outside dry run and are the whole output of a dry run.
    """
    started = time.monotonic()
    stamp = reservation_policy.parse_timestamp(now) or reservation_policy.now_utc()
    stamp_iso = stamp.isoformat(timespec="seconds")
    rows_limit = int(limit) if limit else batch_limit()

    provider_calls = {"count": 0}
    base_fetch = fetch_status

    def _counting_fetch(intent_id):
        """Count every provider read so the bound is measurable, not asserted.

        Wrapped rather than tallied at the call site because
        ``decide_for_reservation`` decides internally whether a call is needed
        at all — a healthy row and a row with no intent never reach here. The
        count is therefore evidence about the decision table, not about this
        module's intentions.
        """
        provider_calls["count"] += 1
        if base_fetch is not None:
            return base_fetch(intent_id)
        return reconciler._fetch_payment_intent_status(intent_id)

    result = _empty_result(dry_run=dry_run, limit=rows_limit)

    # The worker is a separate process from the web app and may never serve an
    # HTTP request, so it cannot inherit the lazy migration that cart routes
    # perform. It bootstraps its own schema instead. This is the only write the
    # sweep performs in dry-run mode, and it is deliberate: creating a nullable
    # column and an index mutates no reservation, and without it a dry run can
    # never measure anything, which makes the dry run itself uninformative.
    schema_state = reservation_schema.ensure_reservation_schema(cur)
    schema_status = schema_state.get("status")
    if schema_status != reservation_schema.STATUS_READY:
        reason = (REASON_SCHEMA_MISSING
                  if schema_status == reservation_schema.STATUS_MISSING
                  else REASON_SCHEMA_ENSURE_FAILED)
        LOGGER.error(
            "RESERVATION_SWEEP_SCHEMA_BLOCKED reason=%s missing=%s error=%s dry_run=%s",
            reason, ",".join(schema_state.get("missing") or ()) or "-",
            schema_state.get("error"), dry_run)
        return _degraded(result, reason=reason, started=started,
                         detail=schema_state.get("error"),
                         missing=schema_state.get("missing") or ())

    try:
        candidates = select_expiry_candidates(
            cur, now=stamp, limit=rows_limit, recheck_seconds=recheck_seconds)
    except reservation_schema.ReservationSchemaMissing as exc:
        # The ensure said ready — from its process cache — and the table
        # disagrees. Clear the cache so the next maintenance interval re-runs
        # the DDL rather than short-circuiting past it for the life of the
        # process.
        reservation_schema.reset_schema_cache()
        LOGGER.error("RESERVATION_SWEEP_SCHEMA_MISSING missing=%s dry_run=%s",
                     ",".join(exc.missing), dry_run)
        return _degraded(result, reason=REASON_SCHEMA_MISSING, started=started,
                         detail=str(exc), missing=exc.missing)
    except Exception as exc:
        LOGGER.exception("RESERVATION_SWEEP_CANDIDATE_QUERY_FAILED")
        return _degraded(result, reason=REASON_CANDIDATE_QUERY_FAILED,
                         started=started, detail=str(exc))

    result["scanned"] = len(candidates)
    result["candidates"] = len(candidates)
    result["batch_exhausted"] = len(candidates) >= rows_limit

    LOGGER.info("RESERVATION_SWEEP_STARTED candidates=%s limit=%s dry_run=%s",
                len(candidates), rows_limit, dry_run)

    for row in candidates:
        tx_id = int(row.get("seller_transaction_id") or 0)
        if tx_id <= 0:
            result["skipped"] += 1
            result["would_skip"] += 1
            continue
        try:
            _process_candidate(cur, row, tx_id, result, stamp_iso=stamp_iso,
                               dry_run=dry_run, fetch=_counting_fetch)
        except Exception:
            # One bad row must not cost the other forty-nine their sweep. The
            # caller gets a partial-success summary and the row is retried on
            # the next cycle, because nothing about it was mutated.
            LOGGER.exception("RESERVATION_FAILED tx_id=%s", tx_id)
            result["failed"] += 1

    result["provider_calls"] = provider_calls["count"]
    result["duration_ms"] = int((time.monotonic() - started) * 1000)
    if result["failed"]:
        # Individual rows raised. The sweep did look — `scanned` and
        # `candidates` are real measurements — so this is a partial success,
        # reported as degraded with no schema reason attached.
        result["status"] = STATUS_DEGRADED
    LOGGER.info(
        "RESERVATION_SWEEP_COMPLETED candidates=%s released=%s captured=%s "
        "deferred=%s skipped=%s failed=%s provider_calls=%s attention=%s "
        "dry_run=%s duration_ms=%s",
        result["candidates"], result["released"], result["captured"],
        result["deferred"], result["skipped"], result["failed"],
        result["provider_calls"], result["needs_attention"],
        dry_run, result["duration_ms"],
    )
    return result


def _process_candidate(cur, row: dict, tx_id: int, result: dict, *,
                       stamp_iso: str, dry_run: bool, fetch) -> None:
    """Decide and act on exactly one reservation.

    Split out so the failure boundary in the caller wraps a whole candidate:
    every path through here either completes or raises, and a raise leaves the
    row untouched and eligible for the next sweep.
    """
    deferrals = int(row.get("reconcile_deferrals") or 0)
    decision = reconciler.decide_for_reservation(
        row, fetch_status=fetch, deferrals=deferrals)
    verdict = decision.get("decision")
    intent_status = decision.get("payment_intent_status")
    detail = decision.get("detail") or ""

    if decision.get("needs_attention"):
        result["needs_attention"] += 1

    LOGGER.debug(
        "RESERVATION_CANDIDATE tx_id=%s expires_at=%s deferrals=%s decision=%s "
        "payment_intent_status=%s detail=%s",
        tx_id, row.get("expires_at"), deferrals, verdict, intent_status, detail)

    if intent_status:
        # A provider answer was actually consulted for this row, whether or not
        # it changed the outcome.
        result["reconciled"] += 1
        LOGGER.debug("RESERVATION_RECONCILED tx_id=%s payment_intent_status=%s",
                     tx_id, intent_status)

    if verdict == reconciler.DECISION_RELEASE:
        result["would_release"] += 1
        reason = decision.get("release_reason") or reservation_policy.REASON_EXPIRED
        terminal_status = terminal_status_for(reason)
        if dry_run:
            LOGGER.info(
                "RESERVATION_RELEASED tx_id=%s dry_run=1 reason=%s terminal=%s detail=%s",
                tx_id, reason, terminal_status, detail)
            return
        outcomes = cart.settle_failed_transactions(
            cur, [tx_id], reason=reason, terminal_status=terminal_status,
            now=stamp_iso)
        outcome = outcomes[0] if outcomes else {}
        if outcome.get("changed"):
            result["released"] += 1
        else:
            # The hold was captured or released between selection and here.
            # Not an error and not a release: the shared path refused, which is
            # exactly what it exists to do.
            result["skipped"] += 1
        LOGGER.info(
            "RESERVATION_RELEASED tx_id=%s reason=%s terminal=%s released=%s "
            "transaction_updated=%s listing_id=%s quantity=%s detail=%s",
            tx_id, reason, terminal_status, bool(outcome.get("changed")),
            bool(outcome.get("transaction_updated")), outcome.get("listing_id"),
            outcome.get("quantity"), detail)
        return

    if verdict == reconciler.DECISION_CAPTURE:
        # Money moved but the hold is still open, which means a
        # `payment_intent.succeeded` webhook was lost. Consuming the hold is
        # the protective half of the repair: it makes the stock permanently
        # unreturnable, so no later sweep can hand a paid item back.
        #
        # The other half — marking the order paid and creating it — is
        # deliberately not done here. That is the success branch's work, it
        # involves Connect routing and buyer-visible order creation, and a
        # second copy of it living in a sweeper is precisely the duplication
        # Stages 5-6 removed. The row is counted under `needs_attention` so an
        # operator sees a settled payment whose order never materialised,
        # rather than the sweeper silently inventing one.
        result["would_skip"] += 1
        result["needs_attention"] += 1
        if dry_run:
            LOGGER.info("RESERVATION_CANDIDATE tx_id=%s dry_run=1 decision=capture detail=%s",
                        tx_id, detail)
            return
        outcome = cart.capture_inventory_reservation(cur, tx_id, now=stamp_iso)
        if outcome.get("changed"):
            result["captured"] += 1
        else:
            result["skipped"] += 1
        LOGGER.warning(
            "RESERVATION_RECONCILED tx_id=%s decision=capture captured=%s "
            "payment_intent_status=%s detail=%s needs_order_repair=1",
            tx_id, bool(outcome.get("changed")), intent_status, detail)
        return

    # Everything else defers. `decide_for_reservation` returns `defer` for an
    # unreachable provider, an unrecognised status, an asynchronous method still
    # settling, and a buyer still authenticating — four different situations
    # that share one correct response.
    result["would_defer"] += 1
    if dry_run:
        LOGGER.info("RESERVATION_DEFERRED tx_id=%s dry_run=1 detail=%s", tx_id, detail)
        return
    note = cart.note_reservation_deferral(cur, tx_id, now=stamp_iso)
    if note.get("changed"):
        result["deferred"] += 1
    else:
        result["skipped"] += 1
    LOGGER.info(
        "RESERVATION_DEFERRED tx_id=%s deferrals=%s payment_intent_status=%s "
        "detail=%s attention=%s",
        tx_id, note.get("deferrals"), intent_status, detail,
        bool(decision.get("needs_attention")))
