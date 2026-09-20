"""A production caller for ``marketplace_payout_scheduler.run_once``.

``run_once`` has never had one. That was deliberate — it is the function that
moves money out of the platform balance, first to a connected account and then
to a seller's bank, and adding a caller is the commit that turns real money
movement on. This module adds the caller and keeps it switched off, so that
turning it on is a configuration decision an owner makes rather than a code
change someone has to write correctly under time pressure.

Three things have to be true before a cent moves:

1. ``MARKETPLACE_PAYOUT_WORKER_ENABLED`` — the cycle runs at all.
2. ``MARKETPLACE_PAYOUT_WORKER_DRY_RUN`` set false — it may mutate.
3. ``MARKETPLACE_PAYOUT_WORKER_OWNER_AUTHORIZED`` — the owner authorised it.

Those three are the owner's decision. Two further conditions are the
deployment's, and are checked rather than decided: the run has to be on Postgres
(see ``_mutation_preconditions``), and the Stripe key has to say which
environment it belongs to. The switches can record that the owner authorised a
payout run; they cannot record that the owner knew which Stripe it would reach.

Two independent switches guard mutation rather than one because the blast radius
is irreversible: a Stripe transfer to a connected account cannot be taken back by
this platform, only requested back from the seller. One mistyped Railway variable
should not be sufficient, and every flag fails to the non-acting value when it is
unset, blank or unparseable.

**Dry run is read-only.** It does not call ``run_once`` with recording stubs,
because ``run_once`` writes before it ever reaches a provider: it creates a
canonical payout request and transitions the settlement to ``scheduled``. A dry
run that produced those rows would leave real state behind and report itself as
having changed nothing. So the preview answers the question from the settlements
table directly and touches nothing.
"""

from __future__ import annotations

import logging
import os
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Callable, Iterator, Mapping

from services import db
from services import marketplace_payout_scheduler as scheduler
from services import marketplace_settlement_service as settlements
from services import stripe_mode

ENABLED_ENV_VAR = "MARKETPLACE_PAYOUT_WORKER_ENABLED"
DRY_RUN_ENV_VAR = "MARKETPLACE_PAYOUT_WORKER_DRY_RUN"
OWNER_AUTHORIZED_ENV_VAR = "MARKETPLACE_PAYOUT_WORKER_OWNER_AUTHORIZED"
INTERVAL_ENV_VAR = "MARKETPLACE_PAYOUT_WORKER_SECONDS"
BATCH_ENV_VAR = "MARKETPLACE_PAYOUT_WORKER_BATCH"

#: Payouts are not latency sensitive; a seller waiting ten more minutes for money
#: that has already cleared its protection window is not an incident. The floor
#: matters more than the ceiling: each cycle costs up to two Stripe writes per
#: eligible row, so a misconfigured hot loop would be rate-limited by Stripe
#: rather than by us.
DEFAULT_INTERVAL_SECONDS = 600
MIN_INTERVAL_SECONDS = 60
MAX_INTERVAL_SECONDS = 3600

DEFAULT_BATCH = 25
MIN_BATCH = 1
MAX_BATCH = 200

RETRY_MAX_ATTEMPTS_ENV_VAR = "MARKETPLACE_PAYOUT_RETRY_MAX_ATTEMPTS"
RETRY_BASE_SECONDS_ENV_VAR = "MARKETPLACE_PAYOUT_RETRY_BASE_SECONDS"
RETRY_MAX_SECONDS_ENV_VAR = "MARKETPLACE_PAYOUT_RETRY_MAX_SECONDS"

#: Five attempts on a doubling backoff from fifteen minutes covers every
#: transient failure this platform can really have — a Stripe blip, a rate
#: limit, a connection reset — and stops well short of the failures where
#: retrying is the wrong tool entirely. The ceiling exists because an unbounded
#: retry is not persistence, it is a way of never telling anyone. The floor of
#: one exists because zero attempts would make the retry path unreachable, and
#: an unreachable path is an untested one.
DEFAULT_RETRY_MAX_ATTEMPTS = 5
MIN_RETRY_MAX_ATTEMPTS = 1
MAX_RETRY_MAX_ATTEMPTS = 10

#: A seller waiting fifteen more minutes for money that already sat out a
#: multi-day protection window has lost nothing measurable. Retrying in seconds
#: would just spend the whole budget inside one Stripe outage.
DEFAULT_RETRY_BASE_SECONDS = 900
MIN_RETRY_BASE_SECONDS = 60
MAX_RETRY_BASE_SECONDS = 86400

#: Cap on any single wait, so the last attempt of a long budget still lands
#: within a working day instead of next week.
DEFAULT_RETRY_MAX_SECONDS = 21600
MIN_RETRY_MAX_SECONDS = 60
MAX_RETRY_MAX_SECONDS = 604800

#: Distinct from the 620260524 that ``bot.init_db`` uses. Sharing a key would
#: make a long migration and a payout cycle silently exclude each other.
ADVISORY_LOCK_KEY = 620260917

_TRUTHY = frozenset({"1", "true", "t", "yes", "y", "on"})
_FALSEY = frozenset({"0", "false", "f", "no", "n", "off"})


def _env_flag(name: str, *, default: bool) -> bool:
    """Parse a boolean env var. Anything unclear resolves to ``default``.

    Every caller below passes the non-acting value as ``default``, so an
    unparseable flag never becomes the reason money moved.
    """
    raw = (os.getenv(name) or "").strip().lower()
    if raw in _TRUTHY:
        return True
    if raw in _FALSEY:
        return False
    return default


def worker_enabled() -> bool:
    """Whether the cycle runs at all. Off unless explicitly switched on."""
    return _env_flag(ENABLED_ENV_VAR, default=False)


def dry_run() -> bool:
    """Whether the cycle only reports. On unless explicitly switched off."""
    return _env_flag(DRY_RUN_ENV_VAR, default=True)


def owner_authorized() -> bool:
    """The second, independent switch in front of real money movement."""
    return _env_flag(OWNER_AUTHORIZED_ENV_VAR, default=False)


def may_move_money() -> bool:
    """Both mutation gates, evaluated together.

    Kept as one function so there is a single expression in the codebase that
    answers "can this move money", rather than two call sites that could drift
    into checking one flag each.
    """
    return (not dry_run()) and owner_authorized()


def interval_seconds() -> int:
    """Cadence in seconds, clamped so a typo cannot produce a hot loop."""
    return _clamped(INTERVAL_ENV_VAR, DEFAULT_INTERVAL_SECONDS,
                    MIN_INTERVAL_SECONDS, MAX_INTERVAL_SECONDS)


def batch_limit() -> int:
    """Rows per cycle, clamped. ``run_once`` clamps again at 200 independently."""
    return _clamped(BATCH_ENV_VAR, DEFAULT_BATCH, MIN_BATCH, MAX_BATCH)


def retry_policy() -> dict:
    """The bounded retry budget, clamped, as one dict the scheduler can pass on.

    Returned as a value rather than read by the scheduler so the whole policy is
    decided once per cycle. Reading the env per row would let a variable change
    mid-cycle produce two different budgets in one run, and the resulting row
    history would be unexplainable from the configuration.
    """
    return {
        "max_attempts": _clamped(RETRY_MAX_ATTEMPTS_ENV_VAR, DEFAULT_RETRY_MAX_ATTEMPTS,
                                 MIN_RETRY_MAX_ATTEMPTS, MAX_RETRY_MAX_ATTEMPTS),
        "base_seconds": _clamped(RETRY_BASE_SECONDS_ENV_VAR, DEFAULT_RETRY_BASE_SECONDS,
                                 MIN_RETRY_BASE_SECONDS, MAX_RETRY_BASE_SECONDS),
        "max_seconds": _clamped(RETRY_MAX_SECONDS_ENV_VAR, DEFAULT_RETRY_MAX_SECONDS,
                                MIN_RETRY_MAX_SECONDS, MAX_RETRY_MAX_SECONDS),
    }


def _clamped(name: str, default: int, low: int, high: int) -> int:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return max(low, min(value, high))


def _row_value(row: Any) -> bool:
    """Read the ``locked`` column from a row of any shape this repo produces.

    Mirrors ``bot._migration_row_value``, which exists for the identical
    ``pg_try_advisory_lock`` statement in ``init_db``. Row shape is not uniform
    here — ``db.CompatRow`` is a Mapping, ``sqlite3.Row`` supports both key and
    index, and a bare DBAPI cursor yields a tuple with neither ``.get`` nor
    string keys. Getting this wrong fails in only one direction: an exception
    inside the lock would be caught by the cycle's handler and reported as a
    provider failure, so the payout system would look broken rather than
    unlocked.
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

    Railway can run more than one replica of a worker, and two replicas entering
    ``run_once`` together would both read the same eligible rows. The per-row
    database idempotency keys underneath (``payout_key``, ``seller_transfer:…``,
    ``seller_payout:…``) mean the *money* would still move exactly once, so this
    lock is not the thing that prevents a double payout. What it prevents is the
    losing replica spending a cycle generating failures and ``failed`` state
    transitions on rows the winner is already handling, which would look
    identical to a provider outage in the logs.

    On Postgres this is ``pg_try_advisory_lock`` — try, never wait, because a
    replica that blocks here would pile up behind a cycle that is already doing
    the work it wanted to do. The lock is session-scoped, so it is released
    explicitly and the connection is closed as a backstop.

    On SQLite there is no cluster to coordinate, and no way to build one. Rather
    than yield ``True`` and quietly provide no protection, this refuses: see
    ``_mutation_preconditions``, which does not allow money movement off
    Postgres at all.
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
                # The connection close below drops the session and with it the
                # lock. Logged rather than raised: failing to unlock must not
                # mask whatever the cycle itself reported.
                logging.exception("PAYOUT_WORKER_UNLOCK_FAILED key=%s", ADVISORY_LOCK_KEY)
        conn.close()


def _mutation_preconditions() -> str:
    """Why this cycle may not move money, or ``""`` if it may.

    Returns a reason rather than a bool so the heartbeat can say which gate is
    shut. "Disabled" and "authorized but running on SQLite" need different
    operator responses, and a bare False conflates them.
    """
    if dry_run():
        return "dry_run"
    if not owner_authorized():
        return "owner_not_authorized"
    if not db.IS_POSTGRES:
        # Not a portability gap. Production is Postgres; a worker moving real
        # money while pointed at a local SQLite file is a misconfiguration, and
        # it is also the only engine where `leader_lock` can actually lock.
        return "no_leader_lock_off_postgres"
    if stripe_mode.mode() == stripe_mode.UNCONFIGURED:
        # Nothing to call. Reported rather than discovered one settlement at a
        # time, since every row in the batch would fail identically.
        return "stripe_not_configured"
    if stripe_mode.mode() == stripe_mode.UNRECOGNIZED:
        # A key whose environment cannot be read is treated as live. The three
        # switches above say the owner authorised *a* payout run; they cannot
        # say the owner knew which Stripe it would reach.
        return "stripe_mode_unrecognized"
    return ""


def blocked_reason() -> str:
    """The public name for "why is this not paying", or ``""`` if it would.

    ``may_move_money`` answers only for the two switches, which is the half an
    operator sets deliberately. The other half -- Postgres, and a Stripe key
    this *service* can see -- is set somewhere else and is the half that gets
    forgotten, because Railway variables are per service and the payout worker
    does not run on the web service.

    Exposed so the boot log and the heartbeat can report the same reason
    without either reaching into a private helper or growing its own copy of
    the ladder.
    """
    return _mutation_preconditions()


def resolve_account(seller_id: str) -> Mapping[str, Any]:
    """The seller's current Connect snapshot, read fresh every cycle.

    Deliberately not cached and not taken from the settlement row. A settlement
    became ``eligible`` using the capability flags as they were at sale time; by
    the time it is paid the seller may have deauthorized PulseSoc or had payouts
    disabled by Stripe. ``request_payout`` refuses a snapshot without
    ``payouts_enabled``, so reading it fresh is what turns a revoked account into
    a skipped row instead of a failed Stripe call.
    """
    conn = db.connect()
    try:
        row = conn.execute(
            """SELECT connected_account_id, provider_account_id, payouts_enabled,
                      charges_enabled, onboarding_status
               FROM seller_payout_accounts WHERE user_id=?
               ORDER BY CASE WHEN COALESCE(payouts_enabled,0)=1 THEN 0 ELSE 1 END,
                        updated_at DESC, id DESC LIMIT 1""",
            (int(seller_id),),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return {}
    account = dict(row)
    return {
        "connected_account_id": str(account.get("connected_account_id")
                                    or account.get("provider_account_id") or ""),
        "payouts_enabled": bool(account.get("payouts_enabled")),
        "charges_enabled": bool(account.get("charges_enabled")),
        "onboarding_status": str(account.get("onboarding_status") or ""),
    }


def _transfer_via_stripe(args: Mapping[str, Any]) -> Mapping[str, Any]:
    """Platform balance → connected account.

    ``build_stripe_transfer_args`` already chose a stable idempotency key derived
    from ``payout_key``; passing it through is what makes a retry return Stripe's
    existing transfer rather than creating a second one. ``run_once`` reads
    ``id``, so the provider's response shape is mapped here.
    """
    from services import payment_provider

    args = dict(args or {})
    result = payment_provider.create_transfer(
        idempotency_key=str(args.get("idempotency_key") or ""),
        **dict(args.get("kwargs") or {}),
    )
    if not result.get("ok"):
        raise RuntimeError(f"transfer refused: {result.get('message') or result}")
    return {"id": result.get("provider_transfer_id") or ""}


def _payout_via_stripe(args: Mapping[str, Any]) -> Mapping[str, Any]:
    """Connected account balance → the seller's bank.

    ``stripe_account`` is required here and is correct: a payout is executed *as*
    the connected account. That is the opposite of a charge, where the same
    argument would mean the platform never holds the money at all.
    """
    from services import payment_provider

    args = dict(args or {})
    result = payment_provider.create_payout(
        stripe_account=str(args.get("stripe_account") or ""),
        idempotency_key=str(args.get("idempotency_key") or ""),
        **dict(args.get("kwargs") or {}),
    )
    if not result.get("ok"):
        raise RuntimeError(f"payout refused: {result.get('message') or result}")
    return {"id": result.get("provider_payout_id") or ""}


def _reverse_transfer_via_stripe(args: Mapping[str, Any]) -> Mapping[str, Any]:
    """Connected account → platform balance: the transfer leg, undone.

    The third money movement on these rails and the only one that runs backwards.
    It is reachable from exactly one place — a refund on an order whose transfer
    has landed but whose payout has not — because that is the only window in
    which the money is still somewhere this platform can reach. Called on a
    settled payout it fails, and it is supposed to: that is a different problem
    with a different answer, not a call to try harder at.
    """
    from services import payment_provider

    args = dict(args or {})
    result = payment_provider.create_transfer_reversal(
        transfer_id=str(args.get("transfer_id") or ""),
        idempotency_key=str(args.get("idempotency_key") or ""),
        **dict(args.get("kwargs") or {}),
    )
    if not result.get("ok"):
        raise RuntimeError(f"transfer reversal refused: {result.get('message') or result}")
    return {"id": result.get("provider_reversal_id") or ""}


def preview(limit: int | None = None) -> dict:
    """What a mutating cycle would pay out, without writing anything.

    This is the dry-run path and the GO/NO-GO evidence: an operator reads
    ``would_pay_count`` and ``would_pay_minor`` here before setting the two
    mutation flags. The filter is deliberately the same predicate ``run_once``
    selects on, so the preview cannot report a different population than the
    cycle it is previewing.
    """
    rows_limit = max(MIN_BATCH, min(int(limit if limit is not None else batch_limit()), MAX_BATCH))
    # `run_once` does this before its own identical query. Without it the preview
    # raises `no such table` on a deployment where nothing has settled yet -
    # which is exactly the deployment where an operator runs the preview first,
    # and a crash there reads as "the payout system is broken" rather than
    # "nothing is waiting to be paid".
    settlements.ensure_schema()
    conn = db.connect()
    try:
        rows = [dict(r) for r in conn.execute(
            """SELECT seller_transaction_id, seller_id, net_seller_earnings_minor, currency
               FROM marketplace_commercial_settlements
               WHERE payout_state='eligible' AND payout_ready=1 AND blocker_code IS NULL
               ORDER BY seller_transaction_id LIMIT ?""",
            (rows_limit,)).fetchall()]
    finally:
        conn.close()
    return {
        "would_pay_count": len(rows),
        "would_pay_minor": sum(int(r["net_seller_earnings_minor"] or 0) for r in rows),
        "sellers": len({r["seller_id"] for r in rows}),
        "currencies": sorted({str(r["currency"] or "") for r in rows}),
    }


def run_cycle(*, account_resolver: Callable[[str], Mapping[str, Any]] | None = None,
              provider_transfer: Callable[[Mapping[str, Any]], Mapping[str, Any]] | None = None,
              provider_create: Callable[[Mapping[str, Any]], Mapping[str, Any]] | None = None,
              provider_reversal: Callable[[Mapping[str, Any]], Mapping[str, Any]] | None = None) -> dict:
    """One cycle: preview, or take the lock and pay.

    Two jobs under one lock: pay out what is owed to sellers, and claw back what
    a refund has made no longer theirs. They share the lock because they read and
    write the same settlement rows and the same connected-account balances, and
    two replicas doing one each would race over both.

    The injectable arguments exist so tests can drive the mutating path without a
    Stripe key. They default to the real Stripe-backed callables, which means a
    production caller cannot accidentally get stubs by omitting them.
    """
    blocked = _mutation_preconditions()
    if blocked:
        return {"status": "preview", "moved_money": False, "reason": blocked, **preview()}

    with leader_lock() as leading:
        if not leading:
            # Not an error. Another replica holds the cycle; this one declines
            # and will try again at its next deadline.
            return {"status": "skipped", "moved_money": False, "reason": "not_leader"}
        metrics = scheduler.run_once(
            account_resolver=account_resolver or resolve_account,
            provider_transfer=provider_transfer or _transfer_via_stripe,
            provider_create=provider_create or _payout_via_stripe,
            limit=batch_limit(),
            retry_policy=retry_policy(),
        )
        # The second job is isolated from the first. By the time it runs the
        # payout pass has already moved real money, and its metrics are the only
        # record of what moved; letting a clawback failure propagate would
        # discard that record and make the cycle look like it never ran. The
        # failure is not swallowed - it is logged and carried out in the result,
        # so the heartbeat shows a recovery queue that has stopped draining.
        try:
            recovery = scheduler.run_refund_recovery_once(
                provider_reversal=provider_reversal or _reverse_transfer_via_stripe,
                limit=batch_limit(),
            )
        except Exception as exc:  # noqa: BLE001 - reported, not hidden
            logging.exception("PAYOUT_WORKER_REFUND_RECOVERY_FAILED error=%s", exc)
            recovery = {"status": "error", "error": f"{type(exc).__name__}: {exc}"}
    moved = bool(metrics.get("transferred_count")) or bool(recovery.get("reversed_count"))
    return {"status": "ok", "moved_money": moved, "refund_recovery": recovery, **metrics}


def run_payout_cycle_if_due(state: dict) -> dict | None:
    """Run one cycle if its own monotonic deadline has passed, else ``None``.

    Monotonic rather than a cycle count, for the same reason the reservation
    sweep uses one: a host loop's real period is ``sleep + however long the host
    took``, so counting cycles would let a busy host stretch the payout interval
    silently. The deadline advances in ``finally`` so a cycle that raises waits a
    full interval instead of retrying every host tick.
    """
    if not worker_enabled():
        return None

    interval = interval_seconds()
    due_at = state.get("payout_cycle_due_at")
    if due_at is not None and time.monotonic() < due_at:
        return None

    try:
        outcome = run_cycle()
    except Exception as exc:
        # A failed cycle is an incident for payouts, not for the host worker.
        logging.exception("PAYOUT_WORKER_CYCLE_FAILED interval=%s error=%s", interval, exc)
        outcome = {"status": "error", "moved_money": False, "error": str(exc)[:500]}
    finally:
        state["payout_cycle_due_at"] = time.monotonic() + interval

    logging.info("PAYOUT_WORKER_CYCLE status=%s moved_money=%s outcome=%s",
                 outcome.get("status"), outcome.get("moved_money"), outcome)
    state["payout_cycle_last"] = _cycle_metrics(outcome)
    return outcome


def _cycle_metrics(outcome: Mapping[str, Any]) -> dict:
    """Flatten one cycle into heartbeat fields."""
    return {
        "last_cycle_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "last_cycle_status": outcome.get("status"),
        # Present on every preview and skip, absent on a real cycle, so its
        # presence is itself the answer to "why did nothing get paid".
        "last_cycle_reason": outcome.get("reason"),
        "last_cycle_moved_money": bool(outcome.get("moved_money")),
        "last_cycle_eligible": outcome.get("eligible_count", outcome.get("would_pay_count", 0)),
        "last_cycle_scheduled": outcome.get("scheduled_count", 0),
        "last_cycle_transferred": outcome.get("transferred_count", 0),
        "last_cycle_paid": outcome.get("paid_count", 0),
        "last_cycle_failed": outcome.get("failed_count", 0),
        "last_cycle_duplicate_prevention": outcome.get("duplicate_prevention", 0),
        "last_cycle_would_pay_minor": outcome.get("would_pay_minor", 0),
        "last_cycle_duration_ms": outcome.get("job_duration_ms", 0),
    }


def heartbeat_metadata(state: dict) -> dict:
    """Payout fields for the host worker's heartbeat, read from ``state``.

    ``record_worker_heartbeat`` replaces ``metadata_json`` wholesale, and this
    cycle runs on roughly one host tick in thirty. Reporting only the current
    tick would blank ``last_cycle_at`` in between, which reads identically to a
    worker that never ran.
    """
    if not worker_enabled():
        return {"payout_worker_enabled": False}
    # Evaluated once. Two calls could disagree if a flag were read between
    # them, and "may move money" and "blocked by" disagreeing is the one pair
    # of fields an operator would never think to distrust.
    blocked = blocked_reason()
    return {
        "payout_worker_enabled": True,
        "payout_worker_interval": interval_seconds(),
        # The honest headline: enabled does not mean paying.
        "payout_worker_may_move_money": not blocked,
        "payout_worker_blocked_by": blocked or None,
        # Which Stripe the money would go to. An operator reading a heartbeat
        # that says it is paying should not have to look up a key to find out.
        "payout_worker_stripe_mode": stripe_mode.mode(),
        **state.get("payout_cycle_last", {}),
    }
