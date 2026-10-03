"""A production caller for ``bot.pulse_reconcile_missed_marketplace_payments``.

The sweep itself has been correct and unreachable. It asks Stripe whether a
payment this server never recorded actually happened, and it refuses to act on
anything short of ``succeeded`` — but it defaults to ``dry_run=True`` and no
deployed process has ever called it. The August incident it was written for
(a live $0.50 PaymentIntent that succeeded at Stripe, whose webhook never
arrived, which nobody noticed for 37 days, by which time Stripe's event
retention had passed and the event could never be replayed) would happen again
in exactly the same way today.

Turning it on is not a matter of flipping ``dry_run``. A sweep that repairs
credits a seller, and one that runs on a timer in more than one replica, against
a provider that can be down, over a queue it reads oldest-first under a
``LIMIT``, needs a control plane before it needs permission:

**Cadence and bounded batches.** Every cycle costs one Stripe read per
candidate, so the interval has a floor and the batch has a ceiling, both
clamped, so that a mistyped Railway variable cannot produce a hot loop against
a rate-limited API.

**Single-run protection.** Railway can run more than one replica. The writes
underneath are idempotent — ``pulse_settle_marketplace_payment_intent`` is the
same call the webhook makes, and settling twice settles once — so the lock is
not what prevents a double order. What it prevents is two replicas spending
their cycles fetching the same intents and filing the same incidents, which in
the logs is indistinguishable from a provider in trouble.

**Backoff, and the starvation it actually fixes.** This is the part that is not
politeness. Candidates come back oldest-first under a ``LIMIT``, so without
deferral a handful of long-abandoned checkouts at the head of the queue occupy
every slot of every batch forever, and a payment genuinely lost today is never
examined at all. The sweep would run on schedule, report nothing wrong, and be
blind to the one case it exists for. So each examined row that was not repaired
is written to a ledger with a next-attempt time on a doubling backoff, and a
bounded number of attempts after which it stops being asked about.

The one row that is never allowed to run out is the one Stripe already called
``succeeded``. That row is not being retried, it is waiting on a permission, and
a budget applied to waiting retires recoverable money — under
``abandoned_unpaid``, the precise opposite of what Stripe said. It backs off so
report-only does not refile it every cycle, and it keeps its place forever.

**Terminal versus in-flight.** A ``canceled`` PaymentIntent is the only
non-succeeded status Stripe never revives; it is retired on sight. A
``processing`` or ``requires_payment_method`` intent can still become
``succeeded``, so it keeps its full retry budget. Collapsing the two would
either poll the dead forever or abandon the living.

**What exhaustion means is different per outcome,** which is why the ledger
stores the reason. Giving up on an abandoned checkout is the normal end of its
life and is not news. Giving up on a row whose provider was never reachable
means this server still does not know whether that money moved, which is the
August failure in miniature, so that one files an incident.

Repairing stays gated the way money movement is gated everywhere else here:
three owner switches, every one of them failing to the non-acting value, plus
two conditions that are checked rather than decided — Postgres (the only engine
where the lock can lock) and a Stripe key whose environment can be read. The
switches can record that the owner authorised a repair run; they cannot record
that the owner knew which Stripe it would reach.

Reporting is the default. A cycle that is enabled but not authorised still runs,
still asks Stripe, still files incidents, and writes nothing — which is the mode
that would have caught August, and is worth having on its own.
"""

from __future__ import annotations

import logging
import os
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterable, Iterator, Mapping

from services import db
from services import stripe_mode

ENABLED_ENV_VAR = "MARKETPLACE_MISSED_PAYMENT_ENABLED"
DRY_RUN_ENV_VAR = "MARKETPLACE_MISSED_PAYMENT_DRY_RUN"
OWNER_AUTHORIZED_ENV_VAR = "MARKETPLACE_MISSED_PAYMENT_OWNER_AUTHORIZED"
INTERVAL_ENV_VAR = "MARKETPLACE_MISSED_PAYMENT_SECONDS"
BATCH_ENV_VAR = "MARKETPLACE_MISSED_PAYMENT_BATCH"
GRACE_ENV_VAR = "MARKETPLACE_MISSED_PAYMENT_GRACE_MINUTES"

RETRY_MAX_ATTEMPTS_ENV_VAR = "MARKETPLACE_MISSED_PAYMENT_RETRY_MAX_ATTEMPTS"
RETRY_BASE_SECONDS_ENV_VAR = "MARKETPLACE_MISSED_PAYMENT_RETRY_BASE_SECONDS"
RETRY_MAX_SECONDS_ENV_VAR = "MARKETPLACE_MISSED_PAYMENT_RETRY_MAX_SECONDS"

#: Fifteen minutes. The cost of being late here is measured in how long a lost
#: payment stays lost, and the August incident ran 37 days, so minutes are not
#: the dimension that matters. The floor is what matters: each cycle issues up
#: to ``batch`` Stripe reads, and a sweep looping every second would be
#: rate-limited by Stripe rather than by us.
DEFAULT_INTERVAL_SECONDS = 900
MIN_INTERVAL_SECONDS = 60
MAX_INTERVAL_SECONDS = 21600

#: Small on purpose. Backoff keeps the queue moving, so a batch does not need to
#: be large enough to drain a backlog in one pass — and a modest batch bounds
#: how much a single bad cycle can do.
DEFAULT_BATCH = 25
MIN_BATCH = 1
MAX_BATCH = 200

#: Stripe retries a failing webhook endpoint for hours, so a sweep that examined
#: fresh checkouts would report every one in progress as a lost payment and bury
#: the signal it exists to raise. Mirrors ``MARKETPLACE_RECONCILE_GRACE_MINUTES``,
#: which the sweep uses when this cycle does not override it.
DEFAULT_GRACE_MINUTES = 30
MIN_GRACE_MINUTES = 5
MAX_GRACE_MINUTES = 1440

#: Five attempts doubling from fifteen minutes is about eight hours of asking.
#: A payment genuinely in flight resolves far inside that; a checkout nobody
#: completed does not resolve at all. The ceiling exists because an unbounded
#: retry is not persistence, it is a way of never telling anyone.
DEFAULT_RETRY_MAX_ATTEMPTS = 5
MIN_RETRY_MAX_ATTEMPTS = 1
MAX_RETRY_MAX_ATTEMPTS = 20

DEFAULT_RETRY_BASE_SECONDS = 900
MIN_RETRY_BASE_SECONDS = 60
MAX_RETRY_BASE_SECONDS = 86400

DEFAULT_RETRY_MAX_SECONDS = 21600
MIN_RETRY_MAX_SECONDS = 60
MAX_RETRY_MAX_SECONDS = 604800

#: Distinct from ``marketplace_payout_worker``'s 620260917 and the 620260524
#: ``bot.init_db`` uses. Sharing a key would make a payout cycle and a
#: reconciliation cycle silently exclude each other, and since both are correct
#: to run concurrently that would look like a worker that intermittently skips.
ADVISORY_LOCK_KEY = 620261003

ATTEMPTS_TABLE = "marketplace_missed_payment_attempts"

#: What the last look at a transaction concluded. Named rather than inline
#: because one of them -- ``awaiting_repair`` -- is load-bearing in
#: :func:`record_attempt`, where it is the single outcome exempt from the retry
#: budget, and a typo there would silently retire recoverable money.
OUTCOME_AWAITING_REPAIR = "awaiting_repair"
OUTCOME_UNPAID = "unpaid"
OUTCOME_UNREACHABLE = "unreachable"
OUTCOME_NEEDS_ATTENTION = "needs_attention"

#: Why a transaction stopped being asked about.
EXHAUSTED_PROVIDER_CANCELED = "provider_canceled"
EXHAUSTED_ABANDONED_UNPAID = "abandoned_unpaid"
EXHAUSTED_PROVIDER_UNREACHABLE = "provider_unreachable"
EXHAUSTED_NEEDS_HUMAN = "needs_human"

_TRUTHY = frozenset({"1", "true", "t", "yes", "y", "on"})
_FALSEY = frozenset({"0", "false", "f", "no", "n", "off"})


def _env_flag(name: str, *, default: bool) -> bool:
    """Parse a boolean env var. Anything unclear resolves to ``default``.

    Every caller below passes the non-acting value as ``default``, so an
    unparseable flag never becomes the reason a seller got credited.
    """
    raw = (os.getenv(name) or "").strip().lower()
    if raw in _TRUTHY:
        return True
    if raw in _FALSEY:
        return False
    return default


def _clamped(name: str, default: int, low: int, high: int) -> int:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return max(low, min(value, high))


def cycle_enabled() -> bool:
    """Whether the cycle runs at all. Off unless explicitly switched on."""
    return _env_flag(ENABLED_ENV_VAR, default=False)


def dry_run() -> bool:
    """Whether the cycle only reports. On unless explicitly switched off."""
    return _env_flag(DRY_RUN_ENV_VAR, default=True)


def owner_authorized() -> bool:
    """The second, independent switch in front of crediting a seller."""
    return _env_flag(OWNER_AUTHORIZED_ENV_VAR, default=False)


def interval_seconds() -> int:
    return _clamped(INTERVAL_ENV_VAR, DEFAULT_INTERVAL_SECONDS,
                    MIN_INTERVAL_SECONDS, MAX_INTERVAL_SECONDS)


def batch_limit() -> int:
    return _clamped(BATCH_ENV_VAR, DEFAULT_BATCH, MIN_BATCH, MAX_BATCH)


def grace_minutes() -> int:
    return _clamped(GRACE_ENV_VAR, DEFAULT_GRACE_MINUTES,
                    MIN_GRACE_MINUTES, MAX_GRACE_MINUTES)


def retry_policy() -> dict:
    """The bounded retry budget, clamped, decided once per cycle.

    Returned as a value rather than read per row so a variable changed mid-cycle
    cannot produce two different budgets in one run — a row history that could
    not be explained from the configuration that produced it.
    """
    return {
        "max_attempts": _clamped(RETRY_MAX_ATTEMPTS_ENV_VAR, DEFAULT_RETRY_MAX_ATTEMPTS,
                                 MIN_RETRY_MAX_ATTEMPTS, MAX_RETRY_MAX_ATTEMPTS),
        "base_seconds": _clamped(RETRY_BASE_SECONDS_ENV_VAR, DEFAULT_RETRY_BASE_SECONDS,
                                 MIN_RETRY_BASE_SECONDS, MAX_RETRY_BASE_SECONDS),
        "max_seconds": _clamped(RETRY_MAX_SECONDS_ENV_VAR, DEFAULT_RETRY_MAX_SECONDS,
                                MIN_RETRY_MAX_SECONDS, MAX_RETRY_MAX_SECONDS),
    }


def backoff_seconds(attempts: int, policy: Mapping[str, Any]) -> int:
    """How long to wait after ``attempts`` failed looks at one transaction.

    Doubling from ``base_seconds``, capped at ``max_seconds``. The exponent is
    clamped before the shift rather than after: ``2 ** attempts`` on an
    unclamped attempt count is an arbitrarily large integer, and computing it
    only to throw it away in ``min()`` is how a retry loop becomes a hang.
    """
    base = max(1, int(policy.get("base_seconds") or DEFAULT_RETRY_BASE_SECONDS))
    ceiling = max(base, int(policy.get("max_seconds") or DEFAULT_RETRY_MAX_SECONDS))
    exponent = min(max(0, int(attempts) - 1), 32)
    return int(min(base * (2 ** exponent), ceiling))


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _iso(moment: datetime) -> str:
    """Naive-UTC, seconds precision.

    Matches what ``bot.py`` writes into ``seller_transactions.created_at`` and
    compares against with string inequality. A timezone-aware ISO string sorts
    differently from a naive one at the same instant, and the comparison that
    decides whether a row is due would be wrong in a way no type checker sees.
    """
    return moment.isoformat(timespec="seconds")


def ensure_schema() -> None:
    """Create the attempts ledger if it is absent.

    There is no migration framework here, so this is idempotent DDL run at the
    start of every cycle, following ``incidents.ensure_schema`` and
    ``settlements.ensure_schema`` on the same path. ``seller_transactions`` has
    nowhere to record an attempt count, and adding a column to the table the
    whole money path reads is a larger change than a side ledger keyed on it.
    """
    conn = db.connect()
    try:
        conn.execute(
            f"""CREATE TABLE IF NOT EXISTS {ATTEMPTS_TABLE} (
                    seller_transaction_id INTEGER PRIMARY KEY,
                    payment_intent_id TEXT DEFAULT '',
                    attempts INTEGER NOT NULL DEFAULT 0,
                    outcome TEXT DEFAULT '',
                    provider_status TEXT DEFAULT '',
                    first_seen_at TEXT DEFAULT '',
                    last_attempt_at TEXT DEFAULT '',
                    next_attempt_at TEXT DEFAULT '',
                    exhausted_at TEXT DEFAULT '',
                    exhausted_reason TEXT DEFAULT ''
                )"""
        )
        conn.commit()
    finally:
        conn.close()


def deferred_transaction_ids(now: datetime | None = None) -> set[int]:
    """Transactions the sweep should not spend a batch slot on this cycle.

    Two populations: retired rows, and rows whose next attempt is still in the
    future. Returned as a set for the sweep to exclude in SQL — the exclusion
    has to happen before the ``LIMIT`` or it does not solve anything, which is
    why this is a set of ids and not a filter applied to results.
    """
    moment = _iso(now or _now())
    conn = db.connect()
    try:
        rows = conn.execute(
            f"""SELECT seller_transaction_id FROM {ATTEMPTS_TABLE}
                WHERE COALESCE(exhausted_at,'') != ''
                   OR COALESCE(next_attempt_at,'') > ?""",
            (moment,),
        ).fetchall()
    finally:
        conn.close()
    return {int(dict(row).get("seller_transaction_id") or 0) for row in rows} - {0}


def _ledger_row(conn: Any, tx_id: int) -> dict:
    row = conn.execute(
        f"SELECT * FROM {ATTEMPTS_TABLE} WHERE seller_transaction_id=?", (tx_id,)
    ).fetchone()
    return dict(row) if row is not None else {}


def record_attempt(tx_id: int, *, outcome: str, policy: Mapping[str, Any],
                   payment_intent_id: str = "", provider_status: str = "",
                   terminal: bool = False, now: datetime | None = None) -> dict:
    """Record one look at one transaction and decide when to look again.

    Read-then-write rather than an upsert. ``ON CONFLICT`` is spelled
    differently across the two engines this runs on and there is no migration
    framework to normalise it; the concurrent writer that would justify the
    atomic form is already excluded by the cycle's leader lock, so the simpler
    portable pair is the honest trade.

    ``terminal`` retires a row immediately regardless of its remaining budget:
    a ``canceled`` PaymentIntent has no future to wait for.

    One outcome is exempt from the budget entirely. ``awaiting_repair`` means
    Stripe said ``succeeded`` and this cycle was not permitted to act on it, so
    the attempt count is not measuring a retry — it is counting how many times
    the server has confirmed, correctly, that money is sitting unrecovered. A
    budget applied there retires recoverable money, and retires it under
    ``abandoned_unpaid``: the exact August failure, relabelled as its own
    opposite, and reintroduced by the ledger built to prevent it. It keeps its
    backoff so report-only does not refile every fifteen minutes, but it never
    runs out of looks.
    """
    moment = now or _now()
    stamp = _iso(moment)
    conn = db.connect()
    try:
        existing = _ledger_row(conn, tx_id)
        attempts = int(existing.get("attempts") or 0) + 1
        max_attempts = max(1, int(policy.get("max_attempts") or DEFAULT_RETRY_MAX_ATTEMPTS))

        if outcome == OUTCOME_AWAITING_REPAIR:
            # Never retired. See the docstring: this row is money the provider
            # has already confirmed, and no number of looks makes it stale.
            exhausted_reason = ""
        elif terminal:
            exhausted_reason = EXHAUSTED_PROVIDER_CANCELED
        elif outcome == OUTCOME_NEEDS_ATTENTION:
            # A metadata shape this sweep does not recognise is not a transient
            # fault and asking Stripe again returns the same answer. It needs a
            # person, and the sweep has already logged it at error level.
            exhausted_reason = EXHAUSTED_NEEDS_HUMAN
        elif attempts >= max_attempts:
            exhausted_reason = (EXHAUSTED_PROVIDER_UNREACHABLE
                                if outcome == OUTCOME_UNREACHABLE else EXHAUSTED_ABANDONED_UNPAID)
        else:
            exhausted_reason = ""

        if exhausted_reason:
            exhausted_at, next_attempt_at = stamp, ""
        else:
            exhausted_at = ""
            next_attempt_at = _iso(moment + timedelta(seconds=backoff_seconds(attempts, policy)))

        values = {
            "payment_intent_id": str(payment_intent_id or existing.get("payment_intent_id") or ""),
            "attempts": attempts,
            "outcome": str(outcome or ""),
            "provider_status": str(provider_status or ""),
            "first_seen_at": str(existing.get("first_seen_at") or stamp),
            "last_attempt_at": stamp,
            "next_attempt_at": next_attempt_at,
            "exhausted_at": exhausted_at,
            "exhausted_reason": exhausted_reason,
        }
        if existing:
            conn.execute(
                f"""UPDATE {ATTEMPTS_TABLE} SET payment_intent_id=?, attempts=?, outcome=?,
                        provider_status=?, first_seen_at=?, last_attempt_at=?,
                        next_attempt_at=?, exhausted_at=?, exhausted_reason=?
                    WHERE seller_transaction_id=?""",
                (*values.values(), tx_id),
            )
        else:
            conn.execute(
                f"""INSERT INTO {ATTEMPTS_TABLE} (payment_intent_id, attempts, outcome,
                        provider_status, first_seen_at, last_attempt_at, next_attempt_at,
                        exhausted_at, exhausted_reason, seller_transaction_id)
                    VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (*values.values(), tx_id),
            )
        conn.commit()
    finally:
        conn.close()
    return {"seller_transaction_id": tx_id, **values}


def clear_attempts(tx_id: int) -> None:
    """Forget a transaction's retry history.

    Called after a repair. The row leaves the candidate population anyway once
    its status is ``paid``, so this is housekeeping rather than correctness —
    but a stale ledger row would make a later audit of why a transaction was
    deferred read as though it still were.
    """
    conn = db.connect()
    try:
        conn.execute(f"DELETE FROM {ATTEMPTS_TABLE} WHERE seller_transaction_id=?", (tx_id,))
        conn.commit()
    finally:
        conn.close()


def _row_value(row: Any) -> bool:
    """Read the ``locked`` column from a row of any shape this repo produces.

    Mirrors ``marketplace_payout_worker._row_value`` and ``bot._migration_row_value``,
    which exist for the identical ``pg_try_advisory_lock`` statement. Row shape
    is not uniform: ``db.CompatRow`` is a Mapping, ``sqlite3.Row`` supports key
    and index, a bare DBAPI cursor yields a tuple with neither.
    """
    if row is None:
        return False
    if hasattr(row, "get"):
        return bool(row.get("locked"))
    try:
        return bool(row["locked"])
    except Exception:
        try:
            return bool(row[0])
        except Exception:
            return False


@contextmanager
def leader_lock() -> Iterator[bool]:
    """Hold a cluster-wide lock for one cycle, or yield ``False``.

    ``pg_try_advisory_lock`` — try, never wait: a replica that blocked here
    would pile up behind a cycle already doing the work it wanted to do, and
    then do it again. Session-scoped, so it is released explicitly with the
    connection close as a backstop.

    On SQLite there is no cluster to coordinate and no way to build one. This
    refuses rather than yielding ``True`` and providing no protection; see
    :func:`_mutation_preconditions`, which does not allow repair off Postgres.
    """
    if not db.IS_POSTGRES:
        yield False
        return

    conn = db.connect()
    acquired = False
    try:
        row = conn.execute("SELECT pg_try_advisory_lock(?) AS locked",
                           (ADVISORY_LOCK_KEY,)).fetchone()
        acquired = _row_value(row)
        yield acquired
    finally:
        if acquired:
            try:
                conn.execute("SELECT pg_advisory_unlock(?)", (ADVISORY_LOCK_KEY,))
                conn.commit()
            except Exception:
                logging.exception("MISSED_PAYMENT_UNLOCK_FAILED key=%s", ADVISORY_LOCK_KEY)
        conn.close()


def _mutation_preconditions() -> str:
    """Why this cycle may not repair, or ``""`` if it may.

    A reason rather than a bool, so the heartbeat can say which gate is shut:
    "report-only" and "authorised but running on SQLite" need different operator
    responses and a bare ``False`` conflates them.

    Note what is *not* here: being unable to repair does not stop the cycle from
    running. Detection is the half that would have caught August, and it is safe
    under every one of these conditions.
    """
    if dry_run():
        return "dry_run"
    if not owner_authorized():
        return "owner_not_authorized"
    if not db.IS_POSTGRES:
        # Not a portability gap. Production is Postgres, and it is the only
        # engine where `leader_lock` can actually lock.
        return "no_leader_lock_off_postgres"
    if stripe_mode.mode() == stripe_mode.UNCONFIGURED:
        return "stripe_not_configured"
    if stripe_mode.mode() == stripe_mode.UNRECOGNIZED:
        # A key whose environment cannot be read is treated as live.
        return "stripe_mode_unrecognized"
    if stripe_mode.mode() == stripe_mode.MIXED:
        # One of the two keys is live and nobody configured that deliberately.
        return "stripe_mode_mixed"
    return ""


def blocked_reason() -> str:
    """The public name for "why is this not repairing", or ``""`` if it would."""
    return _mutation_preconditions()


def _escalate_unreachable(entry: Mapping[str, Any]) -> None:
    """File an incident for a row this server never got an answer about.

    The one exhaustion that is news. An abandoned checkout reaching the end of
    its budget is the normal end of its life; a row whose provider was never
    reachable means this server still does not know whether that money moved,
    which is the August failure with the clock already running.
    """
    try:
        from services.business_os.payments import incidents

        incidents.ensure_schema()
        tx_id = int(entry.get("seller_transaction_id") or 0)
        intent_id = str(entry.get("payment_intent_id") or "")
        incidents.open_incident(
            incidents.MISSING_WEBHOOK_EVENT, "seller_payments", severity="critical",
            summary=(f"Gave up asking Stripe about seller transaction {tx_id}; "
                     "payment status is still unknown"),
            details={"seller_transaction_id": tx_id, "payment_intent_id": intent_id,
                     "attempts": entry.get("attempts"),
                     "first_seen_at": entry.get("first_seen_at"),
                     "exhausted_reason": entry.get("exhausted_reason")},
            related_object=f"seller_transaction:{tx_id}", stripe_ref=intent_id,
            incident_key=f"marketplace:reconcile_exhausted:{tx_id}:{intent_id}")
    except Exception:
        # The log line is the fallback record. A reporting failure must not be
        # the thing that ends the cycle.
        logging.exception("MISSED_PAYMENT_ESCALATION_FAILED entry=%s", dict(entry))


def run_cycle(*, fetch_payment_intent: Callable[[str], Any] | None = None,
              now: datetime | None = None) -> dict:
    """One cycle: ask Stripe about due candidates, then record what it said.

    Detection runs whether or not repair is permitted — that asymmetry is the
    point, because the sweep's value in report-only mode is the alarm that was
    missing for 37 days. ``_mutation_preconditions`` decides only whether the
    sweep may write, and is reported either way.

    The lock is taken for the mutating path only. A report-only cycle writes
    nothing but the attempts ledger, and two replicas each filing the same
    already-deduplicated incident is not worth refusing a detection pass over.
    """
    import bot  # Local: importing bot connects and runs init_db at module scope.

    ensure_schema()
    moment = now or _now()
    policy = retry_policy()
    blocked = _mutation_preconditions()
    limit = batch_limit()
    deferred_before = deferred_transaction_ids(moment)

    def _sweep() -> dict:
        return bot.pulse_reconcile_missed_marketplace_payments(
            limit=limit,
            grace_minutes=grace_minutes(),
            dry_run=bool(blocked),
            fetch_payment_intent=fetch_payment_intent,
            skip_transaction_ids=deferred_before,
        )

    if blocked:
        report = _sweep()
    else:
        with leader_lock() as leading:
            if not leading:
                # Not an error. Another replica holds the cycle; this one
                # declines and tries again at its next deadline.
                return {"status": "skipped", "repaired_count": 0, "reason": "not_leader",
                        "deferred_count": len(deferred_before)}
            report = _sweep()

    outcome = _record_report(report, policy=policy, now=moment)
    return {
        "status": "ok",
        "reason": blocked or None,
        "repair_permitted": not blocked,
        "deferred_count": len(deferred_before),
        **outcome,
        **{k: report.get(k) for k in ("examined", "dry_run")},
    }


def _record_report(report: Mapping[str, Any], *, policy: Mapping[str, Any],
                   now: datetime) -> dict:
    """Write one sweep's findings to the ledger and count what happened.

    Every examined row lands in exactly one branch. A row that fell through
    without being recorded would keep its old next-attempt time and be asked
    about again immediately, which is the starvation this ledger exists to stop.
    """
    repaired = [dict(entry) for entry in report.get("repaired") or []]
    applied = [entry for entry in repaired if entry.get("applied")]
    for entry in applied:
        clear_attempts(int(entry.get("transaction_id") or 0))

    # A detected-but-not-repaired row is a *finding*, not a failure to look. It
    # keeps its backoff so the report-only cycle does not refile the same
    # incident every fifteen minutes for the rest of the deployment -- but it
    # keeps its place in the queue permanently, because `record_attempt` exempts
    # this outcome from the retry budget. Money Stripe has already confirmed
    # does not become less recoverable for having been counted.
    pending_repair = [entry for entry in repaired if not entry.get("applied")]
    for entry in pending_repair:
        record_attempt(int(entry.get("transaction_id") or 0),
                       outcome=OUTCOME_AWAITING_REPAIR,
                       policy=policy, payment_intent_id=str(entry.get("payment_intent_id") or ""),
                       provider_status="succeeded", now=now)

    exhausted: list[dict] = []
    detail = {int(d.get("transaction_id") or 0): d for d in report.get("unpaid_detail") or []}
    for tx_id in report.get("unpaid") or []:
        info = detail.get(int(tx_id), {})
        exhausted.append(record_attempt(
            int(tx_id), outcome=OUTCOME_UNPAID, policy=policy,
            payment_intent_id=str(info.get("payment_intent_id") or ""),
            provider_status=str(info.get("provider_status") or ""),
            terminal=bool(info.get("terminal")), now=now))

    for tx_id in report.get("unreachable") or []:
        exhausted.append(record_attempt(int(tx_id), outcome=OUTCOME_UNREACHABLE,
                                       policy=policy, now=now))

    for entry in report.get("needs_attention") or []:
        exhausted.append(record_attempt(
            int(dict(entry).get("transaction_id") or 0), outcome=OUTCOME_NEEDS_ATTENTION,
            policy=policy, payment_intent_id=str(dict(entry).get("payment_intent_id") or ""),
            now=now))

    retired = [row for row in exhausted if row.get("exhausted_reason")]
    for row in retired:
        if row.get("exhausted_reason") == EXHAUSTED_PROVIDER_UNREACHABLE:
            _escalate_unreachable(row)

    return {
        "repaired_count": len(applied),
        "detected_count": len(repaired),
        "awaiting_repair_count": len(pending_repair),
        "unpaid_count": len(report.get("unpaid") or []),
        "unreachable_count": len(report.get("unreachable") or []),
        "needs_attention_count": len(report.get("needs_attention") or []),
        "retired_count": len(retired),
        "retired": [{"seller_transaction_id": row.get("seller_transaction_id"),
                     "reason": row.get("exhausted_reason")} for row in retired],
    }


def run_missed_payment_cycle_if_due(state: dict) -> dict | None:
    """Run one cycle if its own monotonic deadline has passed, else ``None``.

    Monotonic rather than a cycle count, for the reason the payout worker and
    the reservation sweep both use one: a host loop's real period is ``sleep``
    plus however long the host took, so counting ticks lets a busy host stretch
    the interval silently. The deadline advances in ``finally``, so a cycle that
    raises waits a full interval instead of retrying on every host tick.
    """
    if not cycle_enabled():
        return None

    interval = interval_seconds()
    due_at = state.get("missed_payment_cycle_due_at")
    if due_at is not None and time.monotonic() < due_at:
        return None

    try:
        outcome = run_cycle()
    except Exception as exc:
        # A failed cycle is an incident for reconciliation, not for the host.
        logging.exception("MISSED_PAYMENT_CYCLE_FAILED interval=%s error=%s", interval, exc)
        outcome = {"status": "error", "repaired_count": 0, "error": str(exc)[:500]}
    finally:
        state["missed_payment_cycle_due_at"] = time.monotonic() + interval

    logging.info("MISSED_PAYMENT_CYCLE status=%s repaired=%s detected=%s outcome=%s",
                 outcome.get("status"), outcome.get("repaired_count"),
                 outcome.get("detected_count"), outcome)
    state["missed_payment_cycle_last"] = _cycle_metrics(outcome)
    return outcome


def _cycle_metrics(outcome: Mapping[str, Any]) -> dict:
    """Flatten one cycle into heartbeat fields."""
    return {
        "last_cycle_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "last_cycle_status": outcome.get("status"),
        "last_cycle_reason": outcome.get("reason"),
        "last_cycle_examined": outcome.get("examined", 0),
        # The two that matter most, and they are not the same number. `detected`
        # is how many lost payments this cycle found; `repaired` is how many it
        # was allowed to fix. A report-only deployment shows the first rising
        # and the second flat, which is the state an operator must be able to
        # see without reading a flag.
        "last_cycle_detected": outcome.get("detected_count", 0),
        "last_cycle_repaired": outcome.get("repaired_count", 0),
        "last_cycle_awaiting_repair": outcome.get("awaiting_repair_count", 0),
        "last_cycle_unpaid": outcome.get("unpaid_count", 0),
        "last_cycle_unreachable": outcome.get("unreachable_count", 0),
        "last_cycle_needs_attention": outcome.get("needs_attention_count", 0),
        "last_cycle_retired": outcome.get("retired_count", 0),
        "last_cycle_deferred": outcome.get("deferred_count", 0),
    }


def heartbeat_metadata(state: dict) -> dict:
    """Reconciliation fields for the host worker's heartbeat, read from ``state``.

    ``record_worker_heartbeat`` replaces ``metadata_json`` wholesale and this
    cycle runs on roughly one host tick in forty-five, so reporting only the
    current tick would blank ``last_cycle_at`` in between — which reads
    identically to a cycle that never ran, the exact ambiguity this whole module
    exists to remove.
    """
    if not cycle_enabled():
        return {"missed_payment_cycle_enabled": False}
    # Evaluated once: "may repair" and "blocked by" disagreeing is the one pair
    # of fields an operator would never think to distrust.
    blocked = blocked_reason()
    return {
        "missed_payment_cycle_enabled": True,
        "missed_payment_cycle_interval": interval_seconds(),
        "missed_payment_cycle_batch": batch_limit(),
        # The honest headline: enabled does not mean repairing. Detection runs
        # either way, which is why this is not called "enabled".
        "missed_payment_cycle_may_repair": not blocked,
        "missed_payment_cycle_blocked_by": blocked or None,
        "missed_payment_cycle_stripe_mode": stripe_mode.mode(),
        **state.get("missed_payment_cycle_last", {}),
    }
