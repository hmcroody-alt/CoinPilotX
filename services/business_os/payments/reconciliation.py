"""Reconciliation engine — local projections vs. authoritative sources.

Every check here follows one rule: **detect and report, never repair.** A
mismatch produces a :mod:`incidents` row (idempotent by key, so re-running a
sweep does not spam) and leaves the mismatched record exactly as it found it.
Auto-"fixing" a balance is how books stop being books.

Two kinds of checks:

* **Pure-local invariants** (no network): the ledger balance cache vs. its
  entries, ad-wallet columns vs. their transaction ledger, the webhook inbox's
  dead letters, and the Stripe suspense account. ``run_all`` runs these.
* **Snapshot comparison**: ``reconcile_stripe_snapshot`` takes a list of Stripe
  event dicts *fetched by a caller that has network + keys* (e.g. a worker
  paging ``stripe.Event.list``) and verifies every event reached the inbox. It
  is a pure function over its input — this module never talks to Stripe.

The ad-wallet check mirrors the invariant proven in
``tests/pulse_ads/test_reports_insights_wallet.py`` (all sums over *posted*
``pulse_ad_wallet_transactions``)::

    available      == funding - spend - (refund + chargeback)
    lifetime_spent == spend
    reserved       == max(0, reserve - spend)

That invariant only holds for wallets whose spend was funded entirely from
``available_balance_cents``. Spend draws down promotional/bonus/refund credit
buckets *first* (``SPEND_DRAWDOWN_ORDER`` in ``pulse_ad_payments``), and the
spend transaction records the total amount, not the per-bucket split — so a
wallet with credit-bucket activity cannot be verified from the transaction
ledger alone. Such wallets are counted ``skipped``, never guessed at: a
fabricated incident is as corrosive as a silent fix.

Engine-portable via ``services.db``; does not import ``bot.py``.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping, Optional

from services import db
from services.business_os import ledger
from services.business_os.payments import incidents, webhook_inbox

#: Suspense holding account used by ``stripe_ledger_handler`` for funds that
#: could not be mapped to a user. Nonzero == real money waiting for a human.
SUSPENSE_ACCOUNT = "platform:stripe_suspense"

#: Cents of drift at (or above) which a balance mismatch is critical.
CRITICAL_DRIFT_CENTS = 100

#: A webhook row stuck in 'processing' longer than this is presumed stranded.
STUCK_PROCESSING_SECONDS = 3600

#: Wallet transaction types outside the proven invariant. A wallet that has any
#: of these is unverifiable from the transaction ledger (see module docstring).
_UNPROVEN_WALLET_TX_TYPES = ("credit", "adjustment", "promo_credit", "release_reserve")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _row_to_dict(row) -> Optional[dict]:
    if row is None:
        return None
    try:
        return dict(row)
    except Exception:
        return {k: row[k] for k in row.keys()}


def ensure_schema(conn=None) -> None:
    """Create the run-history table if absent. Idempotent; safe at startup."""
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS reconciliation_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                summary_json TEXT
            )
            """
        )
        if owned:
            conn.commit()
    finally:
        if owned:
            conn.close()


def _table_exists(conn, table: str) -> bool:
    """Portable existence probe: the cheapest query that can only fail if the
    table is absent. Wrapped so a miss never poisons the caller's connection."""
    try:
        conn.execute(f"SELECT 1 FROM {table} LIMIT 1")  # noqa: S608 — constant names only
        return True
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        return False


def _severity_for_drift(drift_cents: int) -> str:
    return "critical" if abs(int(drift_cents)) >= CRITICAL_DRIFT_CENTS else "warning"


# ---------------------------------------------------------------------------
# Ledger balance cache vs. entries
# ---------------------------------------------------------------------------

def reconcile_ledger_balances() -> dict:
    """Recompute every cached ledger balance from its entries; report drift.

    Never writes the cache — :func:`ledger.recompute_balance` exists for a
    human who has *decided* to repair after reading the incident.
    """
    ledger.ensure_schema()
    conn = db.connect()
    try:
        accounts = [
            _row_to_dict(r)
            for r in conn.execute(
                "SELECT account, currency, balance_cents FROM ledger_balances"
            ).fetchall()
        ]
        checked = 0
        mismatches = 0
        incident_ids = []
        for row in accounts:
            account = str(row.get("account") or "")
            currency = str(row.get("currency") or "usd")
            cached = int(row.get("balance_cents") or 0)
            cur = conn.execute(
                "SELECT COALESCE(SUM(signed_amount_cents), 0) AS bal "
                "FROM ledger_entries WHERE account = ? AND currency = ?",
                (account, currency),
            )
            entry_row = cur.fetchone()
            computed = int(
                entry_row["bal"] if hasattr(entry_row, "keys") else entry_row[0]
            )
            checked += 1
            if computed == cached:
                continue
            mismatches += 1
            drift = cached - computed
            incident = incidents.open_incident(
                incidents.BALANCE_MISMATCH,
                domain="ledger",
                severity=_severity_for_drift(drift),
                summary=(
                    f"Ledger balance cache for {account} ({currency}) is "
                    f"{cached} cents but its entries sum to {computed} cents."
                ),
                details={
                    "account": account,
                    "currency": currency,
                    "cached_balance_cents": cached,
                    "computed_balance_cents": computed,
                    "drift_cents": drift,
                },
                related_object=f"ledger_balance:{account}:{currency}",
                incident_key=(
                    f"{incidents.BALANCE_MISMATCH}:ledger:{account}:{currency}:"
                    f"{cached}:{computed}"
                ),
            )
            if incident.get("id"):
                incident_ids.append(incident["id"])
        return {"checked": checked, "mismatches": mismatches, "incidents": incident_ids}
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Ad wallets vs. their transaction ledger
# ---------------------------------------------------------------------------

def reconcile_ad_wallets() -> dict:
    """Verify each ad wallet's cached columns against its posted transactions.

    Applies the invariant proven in the pulse_ads test suite (module docstring)
    and NEVER fixes a balance. Wallets with credit-bucket activity are counted
    ``skipped`` because the invariant is unprovable for them from the
    transaction ledger alone.
    """
    conn = db.connect()
    try:
        if not (
            _table_exists(conn, "pulse_ad_wallets")
            and _table_exists(conn, "pulse_ad_wallet_transactions")
        ):
            return {"checked": 0, "mismatches": 0, "skipped": 0,
                    "negative_balances": 0, "incidents": [], "tables_missing": True}

        wallets = [
            _row_to_dict(r)
            for r in conn.execute("SELECT * FROM pulse_ad_wallets").fetchall()
        ]
        checked = 0
        mismatches = 0
        skipped = 0
        negative_balances = 0
        incident_ids = []
        for wallet in wallets:
            account_id = int(wallet.get("account_id") or 0)

            # Negative-state checks run for EVERY wallet, including the ones the
            # invariant cannot verify — a negative is visible on the cached
            # columns alone, no ledger proof required.
            reserved_now = int(wallet.get("reserved_budget_cents") or 0)
            if reserved_now < 0:
                # A reserve is a hold on money; a negative hold is meaningless
                # and means the release arithmetic went wrong somewhere.
                negative_balances += 1
                incident = incidents.open_incident(
                    incidents.NEGATIVE_BALANCE_DETECTED,
                    domain="ad_wallet",
                    severity="critical",
                    summary=(
                        f"Ad wallet for account {account_id} has a negative "
                        f"reserved budget of {reserved_now} cents."
                    ),
                    details={
                        "account_id": account_id,
                        "wallet_id": int(wallet.get("id") or 0),
                        "reserved_budget_cents": reserved_now,
                    },
                    related_object=f"ad_account:{account_id}",
                    incident_key=(
                        f"{incidents.NEGATIVE_BALANCE_DETECTED}:ad_wallet_reserved:"
                        f"{account_id}:{reserved_now}"
                    ),
                )
                if incident.get("id"):
                    incident_ids.append(incident["id"])
            raw_spendable = (
                int(wallet.get("available_balance_cents") or 0)
                + int(wallet.get("promotional_credits_cents") or 0)
                + int(wallet.get("bonus_credits_cents") or 0)
                + int(wallet.get("refund_credits_cents") or 0)
                - max(0, reserved_now)
            )
            if raw_spendable < 0:
                # The runtime floors spendable at zero so nothing can be spent
                # against a debt — but the debt itself must be recorded, not
                # hidden behind the clamp.
                negative_balances += 1
                incident = incidents.open_incident(
                    incidents.NEGATIVE_BALANCE_DETECTED,
                    domain="ad_wallet",
                    severity="warning",
                    summary=(
                        f"Ad wallet for account {account_id} has a negative "
                        f"spendable position of {raw_spendable} cents; the "
                        f"advertiser owes {-raw_spendable} cents."
                    ),
                    details={
                        "account_id": account_id,
                        "wallet_id": int(wallet.get("id") or 0),
                        "raw_spendable_cents": raw_spendable,
                        "amount_owed_cents": -raw_spendable,
                        "available_balance_cents": int(wallet.get("available_balance_cents") or 0),
                        "reserved_budget_cents": reserved_now,
                    },
                    related_object=f"ad_account:{account_id}",
                    incident_key=(
                        f"{incidents.NEGATIVE_BALANCE_DETECTED}:ad_wallet_spendable:"
                        f"{account_id}:{raw_spendable}"
                    ),
                )
                if incident.get("id"):
                    incident_ids.append(incident["id"])
            sums = {
                str(row["transaction_type"]): int(row["total"] or 0)
                for row in (
                    _row_to_dict(r)
                    for r in conn.execute(
                        "SELECT transaction_type, COALESCE(SUM(amount_cents),0) AS total "
                        "FROM pulse_ad_wallet_transactions "
                        "WHERE account_id = ? AND status = 'posted' "
                        "GROUP BY transaction_type",
                        (account_id,),
                    ).fetchall()
                )
            }
            has_unproven_types = any(sums.get(t) for t in _UNPROVEN_WALLET_TX_TYPES)
            has_credit_buckets = any(
                int(wallet.get(col) or 0)
                for col in (
                    "promotional_credits_cents",
                    "bonus_credits_cents",
                    "refund_credits_cents",
                )
            )
            if has_unproven_types or has_credit_buckets:
                skipped += 1
                continue

            funding = sums.get("funding", 0)
            spend = sums.get("spend", 0)
            refund = sums.get("refund", 0) + sums.get("chargeback", 0)
            reserve = sums.get("reserve", 0)

            expected = {
                "available_balance_cents": funding - spend - refund,
                "lifetime_spent_cents": spend,
                "reserved_budget_cents": max(0, reserve - spend),
                # `reverse_wallet_funding` clamps lifetime_funded at zero when
                # debiting a reversal, mirrored here.
                "lifetime_funded_cents": max(0, funding - refund),
            }
            actual = {col: int(wallet.get(col) or 0) for col in expected}
            checked += 1
            diffs = {
                col: {"expected": expected[col], "actual": actual[col],
                      "drift_cents": actual[col] - expected[col]}
                for col in expected
                if expected[col] != actual[col]
            }
            if not diffs:
                continue
            mismatches += 1
            worst_drift = max(abs(d["drift_cents"]) for d in diffs.values())
            fingerprint = ",".join(
                f"{col}={diffs[col]['actual']}/{diffs[col]['expected']}"
                for col in sorted(diffs)
            )
            incident = incidents.open_incident(
                incidents.BALANCE_MISMATCH,
                domain="ad_wallet",
                severity="critical" if worst_drift >= CRITICAL_DRIFT_CENTS else "warning",
                summary=(
                    f"Ad wallet for account {account_id} disagrees with its "
                    f"posted transactions: {fingerprint}"
                ),
                details={
                    "account_id": account_id,
                    "wallet_id": int(wallet.get("id") or 0),
                    "sums": {k: sums.get(k, 0) for k in
                             ("funding", "spend", "refund", "chargeback", "reserve")},
                    "diffs": diffs,
                },
                related_object=f"ad_account:{account_id}",
                incident_key=(
                    f"{incidents.BALANCE_MISMATCH}:ad_wallet:{account_id}:{fingerprint}"
                ),
            )
            if incident.get("id"):
                incident_ids.append(incident["id"])
        return {"checked": checked, "mismatches": mismatches, "skipped": skipped,
                "negative_balances": negative_balances, "incidents": incident_ids}
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Webhook inbox dead letters / stranded rows
# ---------------------------------------------------------------------------

def reconcile_webhook_inbox(
    *,
    max_retries: int = webhook_inbox.DEFAULT_MAX_RETRIES,
    stuck_after_seconds: int = STUCK_PROCESSING_SECONDS,
) -> dict:
    """Report exhausted (DLQ) webhook rows and rows stranded in 'processing'."""
    webhook_inbox.ensure_schema()
    conn = db.connect()
    try:
        dead = [
            _row_to_dict(r)
            for r in conn.execute(
                "SELECT provider, provider_event_id, event_type, retry_count, "
                "last_error, received_at FROM provider_webhook_events "
                "WHERE status = 'failed' AND retry_count >= ?",
                (int(max_retries),),
            ).fetchall()
        ]
        cutoff = (
            datetime.now(timezone.utc) - timedelta(seconds=int(stuck_after_seconds))
        ).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        stuck = [
            _row_to_dict(r)
            for r in conn.execute(
                "SELECT provider, provider_event_id, event_type, received_at "
                "FROM provider_webhook_events "
                "WHERE status = 'processing' AND received_at < ?",
                (cutoff,),
            ).fetchall()
        ]
    finally:
        conn.close()

    incident_ids = []
    for row in dead:
        provider = str(row.get("provider") or "")
        event_id = str(row.get("provider_event_id") or "")
        incident = incidents.open_incident(
            incidents.WEBHOOK_DLQ_EXHAUSTED,
            domain="webhooks",
            severity="critical",
            summary=(
                f"Webhook {provider}:{event_id} exhausted its "
                f"{int(row.get('retry_count') or 0)} retries and is dead-lettered."
            ),
            details={
                "provider": provider,
                "provider_event_id": event_id,
                "event_type": row.get("event_type") or "",
                "retry_count": int(row.get("retry_count") or 0),
                "last_error": (row.get("last_error") or "")[:500],
            },
            related_object=f"webhook:{provider}:{event_id}",
            stripe_ref=event_id if provider == "stripe" else "",
            incident_key=f"{incidents.WEBHOOK_DLQ_EXHAUSTED}:{provider}:{event_id}",
        )
        if incident.get("id"):
            incident_ids.append(incident["id"])
    for row in stuck:
        provider = str(row.get("provider") or "")
        event_id = str(row.get("provider_event_id") or "")
        incident = incidents.open_incident(
            incidents.RECONCILIATION_FAILURE,
            domain="webhooks",
            severity="warning",
            summary=(
                f"Webhook {provider}:{event_id} has been stuck in 'processing' "
                f"since {row.get('received_at')} — a worker likely crashed mid-run."
            ),
            details={
                "provider": provider,
                "provider_event_id": event_id,
                "event_type": row.get("event_type") or "",
                "received_at": row.get("received_at") or "",
            },
            related_object=f"webhook:{provider}:{event_id}",
            stripe_ref=event_id if provider == "stripe" else "",
            incident_key=f"webhook_stuck_processing:{provider}:{event_id}",
        )
        if incident.get("id"):
            incident_ids.append(incident["id"])
    return {
        "dead_lettered": len(dead),
        "stuck_processing": len(stuck),
        "incidents": incident_ids,
    }


# ---------------------------------------------------------------------------
# Suspense account
# ---------------------------------------------------------------------------

def reconcile_suspense() -> dict:
    """A nonzero suspense balance is real money awaiting a human decision.

    The balance is baked into the incident key so a *changed* balance opens a
    fresh incident even if the previous one was resolved.
    """
    ledger.ensure_schema()
    conn = db.connect()
    try:
        rows = [
            _row_to_dict(r)
            for r in conn.execute(
                "SELECT account, currency, balance_cents FROM ledger_balances "
                "WHERE account = ?",
                (SUSPENSE_ACCOUNT,),
            ).fetchall()
        ]
    finally:
        conn.close()

    incident_ids = []
    held = 0
    for row in rows:
        balance = int(row.get("balance_cents") or 0)
        if balance == 0:
            continue
        held += 1
        currency = str(row.get("currency") or "usd")
        incident = incidents.open_incident(
            incidents.SUSPENSE_FUNDS_HELD,
            domain="ledger",
            severity=_severity_for_drift(balance),
            summary=(
                f"{SUSPENSE_ACCOUNT} holds {balance} cents ({currency}) of "
                "unmapped funds awaiting manual reconciliation."
            ),
            details={
                "account": SUSPENSE_ACCOUNT,
                "currency": currency,
                "balance_cents": balance,
            },
            related_object=f"ledger_account:{SUSPENSE_ACCOUNT}:{currency}",
            incident_key=(
                f"{incidents.SUSPENSE_FUNDS_HELD}:{SUSPENSE_ACCOUNT}:{currency}:{balance}"
            ),
        )
        if incident.get("id"):
            incident_ids.append(incident["id"])
    return {"accounts_with_held_funds": held, "incidents": incident_ids}


# ---------------------------------------------------------------------------
# Seller payouts (Wave B)
# ---------------------------------------------------------------------------

#: A payout that Stripe accepted but has not settled after this long is stale —
#: standard payout timing is measured in days, not weeks.
STALE_PAYOUT_DAYS = 7


def reconcile_seller_payouts(*, stale_after_days: int = STALE_PAYOUT_DAYS) -> dict:
    """Cross-check the seller payout lifecycle against the ledger.

    Three invariants, all detect-and-report:

    1. A row in ``payout_created``/``in_transit`` untouched for more than
       ``stale_after_days`` means Stripe stopped talking to us mid-payout —
       PAYOUT_STATE_CONFLICT (warning).
    2. A nonzero ``seller_payout_pending:<uid>`` balance with **no**
       non-terminal payout row means money is fenced with no live request that
       explains it — ORPHAN_LOCAL_RECORD.
    3. A negative ``seller_payable:<uid>`` balance means a seller was paid more
       than they earned — NEGATIVE_BALANCE_DETECTED (critical).
    """
    from services.business_os.payments import seller_payouts

    ledger.ensure_schema()
    conn = db.connect()
    try:
        table_present = _table_exists(conn, "seller_payout_requests")

        stale = []
        if table_present:
            cutoff = (
                datetime.now(timezone.utc) - timedelta(days=int(stale_after_days))
            ).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
            stale = [
                _row_to_dict(r)
                for r in conn.execute(
                    "SELECT id, payout_key, user_id, amount_cents, status, "
                    "stripe_payout_id, updated_at FROM seller_payout_requests "
                    "WHERE status IN ('payout_created', 'in_transit') "
                    "AND updated_at < ?",
                    (cutoff,),
                ).fetchall()
            ]

        pending_balances = [
            _row_to_dict(r)
            for r in conn.execute(
                "SELECT account, currency, balance_cents FROM ledger_balances "
                "WHERE account LIKE 'seller_payout_pending:%' "
                "AND balance_cents != 0"
            ).fetchall()
        ]
        negative_payables = [
            _row_to_dict(r)
            for r in conn.execute(
                "SELECT account, currency, balance_cents FROM ledger_balances "
                "WHERE account LIKE 'seller_payable:%' AND balance_cents < 0"
            ).fetchall()
        ]

        orphaned = []
        for row in pending_balances:
            account = str(row.get("account") or "")
            user_id = account.split(":", 1)[1] if ":" in account else ""
            live = None
            if table_present:
                placeholders = ",".join(
                    "?" for _ in seller_payouts.NON_TERMINAL_STATUSES
                )
                live = conn.execute(
                    "SELECT 1 FROM seller_payout_requests "
                    f"WHERE user_id = ? AND status IN ({placeholders}) LIMIT 1",
                    (user_id, *sorted(seller_payouts.NON_TERMINAL_STATUSES)),
                ).fetchone()
            if live is None:
                orphaned.append(row)
    finally:
        conn.close()

    incident_ids = []
    for row in stale:
        payout_id = int(row.get("id") or 0)
        status = str(row.get("status") or "")
        incident = incidents.open_incident(
            incidents.PAYOUT_STATE_CONFLICT,
            domain="seller_payments",
            severity="warning",
            summary=(
                f"Seller payout {payout_id} has sat in '{status}' since "
                f"{row.get('updated_at')} — Stripe never reported a terminal state."
            ),
            details={
                "payout_id": payout_id,
                "payout_key": row.get("payout_key") or "",
                "user_id": str(row.get("user_id") or ""),
                "amount_cents": int(row.get("amount_cents") or 0),
                "status": status,
                "stripe_payout_id": row.get("stripe_payout_id") or "",
                "updated_at": row.get("updated_at") or "",
            },
            related_object=f"seller_payout:{payout_id}",
            stripe_ref=str(row.get("stripe_payout_id") or ""),
            incident_key=(
                f"{incidents.PAYOUT_STATE_CONFLICT}:stale:"
                f"{payout_id}:{status}:{row.get('updated_at')}"
            ),
        )
        if incident.get("id"):
            incident_ids.append(incident["id"])

    for row in orphaned:
        account = str(row.get("account") or "")
        currency = str(row.get("currency") or "usd")
        balance = int(row.get("balance_cents") or 0)
        incident = incidents.open_incident(
            incidents.ORPHAN_LOCAL_RECORD,
            domain="seller_payments",
            severity=_severity_for_drift(balance),
            summary=(
                f"{account} holds {balance} cents ({currency}) but no "
                "non-terminal payout request explains the fenced funds."
            ),
            details={
                "account": account,
                "currency": currency,
                "balance_cents": balance,
            },
            related_object=f"ledger_account:{account}:{currency}",
            incident_key=(
                f"{incidents.ORPHAN_LOCAL_RECORD}:payout_pending:"
                f"{account}:{currency}:{balance}"
            ),
        )
        if incident.get("id"):
            incident_ids.append(incident["id"])

    for row in negative_payables:
        account = str(row.get("account") or "")
        currency = str(row.get("currency") or "usd")
        balance = int(row.get("balance_cents") or 0)
        incident = incidents.open_incident(
            incidents.NEGATIVE_BALANCE_DETECTED,
            domain="seller_payments",
            severity="critical",
            summary=(
                f"{account} is negative: {balance} cents ({currency}) — the "
                "seller has been paid more than they earned."
            ),
            details={
                "account": account,
                "currency": currency,
                "balance_cents": balance,
            },
            related_object=f"ledger_account:{account}:{currency}",
            incident_key=(
                f"{incidents.NEGATIVE_BALANCE_DETECTED}:seller_payable:"
                f"{account}:{currency}:{balance}"
            ),
        )
        if incident.get("id"):
            incident_ids.append(incident["id"])

    return {
        "stale_payouts": len(stale),
        "orphaned_pending_balances": len(orphaned),
        "negative_payables": len(negative_payables),
        "incidents": incident_ids,
        "table_missing": not table_present,
    }


# ---------------------------------------------------------------------------
# Ad wallet funding sessions vs. the wallet transaction ledger
# ---------------------------------------------------------------------------

def reconcile_funding_sessions(*, pending_after_hours: int = 24) -> dict:
    """Cross-check funding sessions against the wallet transactions they imply.

    Two failure shapes, both pure-local (no network):

    * A session marked paid (``credited``/``completed``/``paid``) with **no**
      matching ``funding`` transaction: Stripe took the money but the wallet
      never saw it. That is a missing credit — BALANCE_MISMATCH, critical.
    * A session still pre-payment (``created``/``checkout_created``/
      ``pending``) more than ``pending_after_hours`` after creation: the
      checkout was abandoned or the completion webhook never landed. Either is
      worth a look, neither is an emergency — ORPHAN_LOCAL_RECORD, info.

    The funding transaction's idempotency key is
    ``stripe:{event_id}:{funding_session_id}``. The session id is matched by
    exact suffix in Python — a SQL ``LIKE '%:12'`` would also match ``:112``.
    """
    conn = db.connect()
    try:
        if not (
            _table_exists(conn, "pulse_ad_wallet_funding_sessions")
            and _table_exists(conn, "pulse_ad_wallet_transactions")
        ):
            return {"checked": 0, "missing_credits": 0, "stuck_pending": 0,
                    "incidents": [], "tables_missing": True}

        paid = [
            _row_to_dict(r)
            for r in conn.execute(
                "SELECT * FROM pulse_ad_wallet_funding_sessions "
                "WHERE status IN ('credited', 'completed', 'paid')"
            ).fetchall()
        ]
        cutoff = (
            datetime.now(timezone.utc).replace(microsecond=0)
            - timedelta(hours=int(pending_after_hours))
        ).isoformat()
        stuck = [
            _row_to_dict(r)
            for r in conn.execute(
                "SELECT * FROM pulse_ad_wallet_funding_sessions "
                "WHERE status IN ('created', 'checkout_created', 'pending') "
                "AND COALESCE(created_at, '') != '' AND created_at < ?",
                (cutoff,),
            ).fetchall()
        ]

        checked = 0
        missing = []
        for session in paid:
            session_id = int(session.get("id") or 0)
            account_id = int(session.get("account_id") or 0)
            checked += 1
            keys = [
                str(_row_to_dict(r).get("idempotency_key") or "")
                for r in conn.execute(
                    "SELECT idempotency_key FROM pulse_ad_wallet_transactions "
                    "WHERE account_id = ? AND transaction_type = 'funding' "
                    "AND status = 'posted'",
                    (account_id,),
                ).fetchall()
            ]
            matched = any(
                key.startswith("stripe:") and key.rsplit(":", 1)[-1] == str(session_id)
                for key in keys
            )
            if not matched:
                missing.append(session)
    finally:
        conn.close()

    incident_ids = []
    for session in missing:
        session_id = int(session.get("id") or 0)
        account_id = int(session.get("account_id") or 0)
        amount = int(session.get("amount_cents") or 0)
        incident = incidents.open_incident(
            incidents.BALANCE_MISMATCH,
            domain="ad_wallet",
            severity="critical",
            summary=(
                f"Ad wallet funding session {session_id} (account {account_id}) "
                f"is marked '{session.get('status')}' for {amount} cents but no "
                "posted funding transaction credits the wallet."
            ),
            details={
                "funding_session_id": session_id,
                "account_id": account_id,
                "amount_cents": amount,
                "status": str(session.get("status") or ""),
                "created_at": session.get("created_at") or "",
            },
            related_object=f"pulse_ad_wallet_funding_sessions:{session_id}",
            stripe_ref=str(session.get("provider_session_id") or ""),
            incident_key=(
                f"{incidents.BALANCE_MISMATCH}:ad_wallet_funding_session:"
                f"{session_id}:{session.get('status')}:{amount}"
            ),
        )
        if incident.get("id"):
            incident_ids.append(incident["id"])

    for session in stuck:
        session_id = int(session.get("id") or 0)
        account_id = int(session.get("account_id") or 0)
        incident = incidents.open_incident(
            incidents.ORPHAN_LOCAL_RECORD,
            domain="ad_wallet",
            severity="info",
            summary=(
                f"Ad wallet funding session {session_id} (account {account_id}) "
                f"has sat in '{session.get('status')}' since "
                f"{session.get('created_at')} — checkout abandoned or the "
                "completion webhook never arrived."
            ),
            details={
                "funding_session_id": session_id,
                "account_id": account_id,
                "amount_cents": int(session.get("amount_cents") or 0),
                "status": str(session.get("status") or ""),
                "created_at": session.get("created_at") or "",
                "pending_after_hours": int(pending_after_hours),
            },
            related_object=f"pulse_ad_wallet_funding_sessions:{session_id}",
            stripe_ref=str(session.get("provider_session_id") or ""),
            incident_key=(
                f"{incidents.ORPHAN_LOCAL_RECORD}:ad_wallet_funding_session:"
                f"{session_id}:{session.get('status')}"
            ),
        )
        if incident.get("id"):
            incident_ids.append(incident["id"])

    return {"checked": checked, "missing_credits": len(missing),
            "stuck_pending": len(stuck), "incidents": incident_ids}


# ---------------------------------------------------------------------------
# Stripe snapshot (input provided by a networked caller)
# ---------------------------------------------------------------------------

def reconcile_stripe_snapshot(
    events: Iterable[Mapping[str, Any]],
    provider: str = "stripe",
) -> dict:
    """Verify a caller-supplied list of provider events all reached the inbox.

    ``events`` is a list of Stripe event dicts (each needs at least ``id``),
    e.g. one page of ``stripe.Event.list`` fetched by a worker that has network
    access and keys. Pure function over its input — no network here. A missing
    event means the webhook endpoint never persisted it: the platform's books
    are missing a fact Stripe has.
    """
    webhook_inbox.ensure_schema()
    checked = 0
    missing = 0
    incident_ids = []
    conn = db.connect()
    try:
        for event in events or []:
            if not isinstance(event, Mapping):
                continue
            event_id = str(event.get("id") or "").strip()
            if not event_id:
                continue
            checked += 1
            cur = conn.execute(
                "SELECT 1 FROM provider_webhook_events "
                "WHERE provider = ? AND provider_event_id = ?",
                (provider, event_id),
            )
            if cur.fetchone() is not None:
                continue
            missing += 1
            incident = incidents.open_incident(
                incidents.MISSING_WEBHOOK_EVENT,
                domain="webhooks",
                severity="critical",
                summary=(
                    f"Provider event {provider}:{event_id} exists at the provider "
                    "but was never recorded in the webhook inbox."
                ),
                details={
                    "provider": provider,
                    "provider_event_id": event_id,
                    "event_type": str(event.get("type") or ""),
                    "provider_created": event.get("created"),
                },
                related_object=f"webhook:{provider}:{event_id}",
                stripe_ref=event_id if provider == "stripe" else "",
                incident_key=f"{incidents.MISSING_WEBHOOK_EVENT}:{provider}:{event_id}",
            )
            if incident.get("id"):
                incident_ids.append(incident["id"])
    finally:
        conn.close()
    return {"checked": checked, "missing": missing, "incidents": incident_ids}


# ---------------------------------------------------------------------------
# Rewards: Pulse Credit ledger + cash reward lifecycle
# ---------------------------------------------------------------------------

def reconcile_rewards() -> dict:
    """Cross-check the rewards domain. Detect-and-report only.

    1. Per-user ``pulse_credit_ledger`` consistency: SUM(delta) must equal the
       latest ``balance_after`` — BALANCE_MISMATCH (critical).
    2. A reward stuck in ``disbursing`` whose payout ended failed/canceled/
       returned means the bounce notification was missed —
       RECONCILIATION_FAILURE (warning).
    3. A negative credit balance should be impossible (CHECK + code guard) —
       NEGATIVE_BALANCE_DETECTED (critical).
    """
    conn = db.connect()
    try:
        tables_present = _table_exists(conn, "pulse_credit_ledger") and _table_exists(
            conn, "reward_events"
        )
        if not tables_present:
            return {
                "tables_missing": True,
                "balance_mismatches": 0,
                "stuck_disbursing": 0,
                "negative_balances": 0,
                "incidents": [],
            }

        mismatches = [
            _row_to_dict(r)
            for r in conn.execute(
                """
                SELECT l.user_id,
                       SUM(l.delta) AS sum_delta,
                       (SELECT balance_after FROM pulse_credit_ledger x
                        WHERE x.user_id = l.user_id
                        ORDER BY x.id DESC LIMIT 1) AS last_balance
                FROM pulse_credit_ledger l
                GROUP BY l.user_id
                HAVING sum_delta != last_balance
                """
            ).fetchall()
        ]

        stuck = []
        if _table_exists(conn, "seller_payout_requests"):
            stuck = [
                _row_to_dict(r)
                for r in conn.execute(
                    """
                    SELECT r.id, r.event_key, r.user_id, r.amount,
                           r.payout_request_id, p.status AS payout_status
                    FROM reward_events r
                    JOIN seller_payout_requests p ON p.id = r.payout_request_id
                    WHERE r.status = 'disbursing'
                      AND p.status IN ('failed', 'canceled', 'returned')
                    """
                ).fetchall()
            ]

        negatives = [
            _row_to_dict(r)
            for r in conn.execute(
                """
                SELECT user_id, balance_after FROM pulse_credit_ledger
                WHERE id IN (
                    SELECT MAX(id) FROM pulse_credit_ledger GROUP BY user_id
                ) AND balance_after < 0
                """
            ).fetchall()
        ]
    finally:
        conn.close()

    incident_ids = []
    for row in mismatches:
        user_id = str(row.get("user_id") or "")
        sum_delta = int(row.get("sum_delta") or 0)
        last_balance = int(row.get("last_balance") or 0)
        incident = incidents.open_incident(
            incidents.BALANCE_MISMATCH,
            domain="rewards",
            severity="critical",
            summary=(
                f"Pulse Credit ledger for user {user_id} is inconsistent: "
                f"SUM(delta)={sum_delta} but last balance_after={last_balance}."
            ),
            details={
                "user_id": user_id,
                "sum_delta": sum_delta,
                "last_balance_after": last_balance,
            },
            related_object=f"pulse_credit_ledger:{user_id}",
            incident_key=(
                f"{incidents.BALANCE_MISMATCH}:pulse_credits:"
                f"{user_id}:{sum_delta}:{last_balance}"
            ),
        )
        if incident.get("id"):
            incident_ids.append(incident["id"])

    for row in stuck:
        reward_id = int(row.get("id") or 0)
        payout_status = str(row.get("payout_status") or "")
        incident = incidents.open_incident(
            incidents.RECONCILIATION_FAILURE,
            domain="rewards",
            severity="warning",
            summary=(
                f"Reward {reward_id} is still 'disbursing' but its payout "
                f"{row.get('payout_request_id')} ended '{payout_status}' — the "
                "bounce notification was missed."
            ),
            details={
                "reward_id": reward_id,
                "event_key": row.get("event_key") or "",
                "user_id": str(row.get("user_id") or ""),
                "amount_cents": int(row.get("amount") or 0),
                "payout_request_id": int(row.get("payout_request_id") or 0),
                "payout_status": payout_status,
            },
            related_object=f"reward_event:{reward_id}",
            incident_key=(
                f"{incidents.RECONCILIATION_FAILURE}:reward_disbursing:"
                f"{reward_id}:{payout_status}"
            ),
        )
        if incident.get("id"):
            incident_ids.append(incident["id"])

    for row in negatives:
        user_id = str(row.get("user_id") or "")
        balance = int(row.get("balance_after") or 0)
        incident = incidents.open_incident(
            incidents.NEGATIVE_BALANCE_DETECTED,
            domain="rewards",
            severity="critical",
            summary=(
                f"Pulse Credit balance for user {user_id} is negative "
                f"({balance}) — the non-negative guard was bypassed."
            ),
            details={"user_id": user_id, "balance_after": balance},
            related_object=f"pulse_credit_ledger:{user_id}",
            incident_key=(
                f"{incidents.NEGATIVE_BALANCE_DETECTED}:pulse_credits:"
                f"{user_id}:{balance}"
            ),
        )
        if incident.get("id"):
            incident_ids.append(incident["id"])

    return {
        "tables_missing": False,
        "balance_mismatches": len(mismatches),
        "stuck_disbursing": len(stuck),
        "negative_balances": len(negatives),
        "incidents": incident_ids,
    }


# ---------------------------------------------------------------------------
# Marketplace settlement chain
# ---------------------------------------------------------------------------

#: A settlement that reached ``scheduled`` but carries no provider payout id is
#: not merely slow. ``marketplace_payout_scheduler.apply_provider_event`` matches
#: rows *by* ``provider_payout_id``, so until that column is filled no Stripe
#: webhook can move the row out of ``scheduled`` — the seller's money is fenced
#: with nothing left that would ever release it. Short window, because the gap
#: between the ``scheduled`` transition and the id being written is two network
#: calls wide, not hours.
STUCK_SCHEDULED_HOURS = 6

#: ``eligible`` means every gate has already opened and the only thing left is
#: for the payout scheduler to pick the row up. A day of that means the scheduler
#: is not running — which is precisely the defect the release cycle was built to
#: end, so it is worth a standing alarm rather than a one-off fix.
STALLED_ELIGIBLE_HOURS = 24

#: A protection hold whose end has passed by this much, with nothing having
#: advanced it, means the eligibility sweep is not running. Same reasoning.
STALLED_HOLD_HOURS = 24

#: Rows read per run for the arithmetic invariant, which is the one check here
#: with no selective predicate. A table past this is reported as ``scan_truncated``
#: rather than quietly half-checked: an unexamined row must not look like a clean
#: one.
SNAPSHOT_SCAN_LIMIT = 5000

#: Rows reported per stuck-state category in a single run. A systemic stall would
#: otherwise open one incident per order, which buries the finding it is trying
#: to surface. The count is always exact; only the per-row detail is capped.
MARKETPLACE_INCIDENT_CAP = 50


def _marketplace_cutoff(hours: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=int(hours))).strftime(
        "%Y-%m-%dT%H:%M:%S.%fZ")


def reconcile_marketplace_settlements(
    *,
    stuck_scheduled_hours: int = STUCK_SCHEDULED_HOURS,
    stalled_eligible_hours: int = STALLED_ELIGIBLE_HOURS,
    stalled_hold_hours: int = STALLED_HOLD_HOURS,
) -> dict:
    """Check the marketplace settlement chain for rows nothing will ever move.

    The chain — fulfillment, protection hold, eligibility, schedule, transfer,
    payout — is a sequence of separately-governed steps, and every one of them
    can be switched off independently. That is deliberate, but it means the
    characteristic marketplace failure is not a wrong number; it is a row that
    stopped somewhere with the seller's money still on the platform's side and
    nothing running that would advance it. None of the other checks in this
    module can see that, because from the ledger's point of view the money is
    exactly where it should be.

    Five findings, all detect-and-report (nothing here transitions a settlement):

    1. ``scheduled`` with no provider payout id — unreachable by webhook, and so
       permanently stuck. Critical regardless of amount.
    2. ``protection_hold`` with no hold end — can never satisfy the hold, so it
       can never become eligible. Critical.
    3. ``protection_hold`` whose end has long passed — the eligibility sweep is
       not running.
    4. ``eligible``, ready, unblocked and untouched — the payout scheduler is not
       running.
    5. ``net != gross - reversed`` on either the fee or the seller's earnings —
       the arithmetic invariant, asked via
       :func:`marketplace_commercial_operations.snapshot_drift` so that the two
       reconcilers that care about it cannot disagree.

    A deployment with no marketplace tables returns ``tables_missing`` and opens
    nothing: absent is not broken.
    """
    from services import marketplace_commercial_operations as commercial
    from services import marketplace_settlement_service as settlements

    conn = db.connect()
    try:
        if not _table_exists(conn, "marketplace_commercial_settlements"):
            return {"tables_missing": True, "incidents": []}

        def _rows(sql: str, params: tuple = ()) -> list:
            return [_row_to_dict(r) for r in conn.execute(sql, params).fetchall()]

        unreachable = _rows(
            "SELECT seller_transaction_id, seller_id, order_id, currency, "
            "net_seller_earnings_minor, updated_at "
            "FROM marketplace_commercial_settlements "
            "WHERE payout_state = 'scheduled' "
            "AND (provider_payout_id IS NULL OR provider_payout_id = '') "
            "AND updated_at < ? ORDER BY seller_transaction_id",
            (_marketplace_cutoff(stuck_scheduled_hours),),
        )
        hold_end_unknown = _rows(
            "SELECT seller_transaction_id, seller_id, order_id, currency, "
            "net_seller_earnings_minor, updated_at "
            "FROM marketplace_commercial_settlements "
            "WHERE payout_state = 'protection_hold' "
            "AND (protection_ends_at IS NULL OR protection_ends_at = '') "
            "ORDER BY seller_transaction_id",
        )
        # The hold end is the deadline, so it is what the grace period is
        # measured from — `updated_at` would restart the clock on any unrelated
        # write and hide exactly the rows this is looking for.
        hold_elapsed = _rows(
            "SELECT seller_transaction_id, seller_id, order_id, currency, "
            "net_seller_earnings_minor, protection_ends_at "
            "FROM marketplace_commercial_settlements "
            "WHERE payout_state = 'protection_hold' "
            "AND protection_ends_at IS NOT NULL AND protection_ends_at != '' "
            "AND protection_ends_at < ? ORDER BY seller_transaction_id",
            (_marketplace_cutoff(stalled_hold_hours),),
        )
        # The scheduler's own selection predicate, so a row this reports is a row
        # the scheduler would have taken had it run.
        unscheduled = _rows(
            "SELECT seller_transaction_id, seller_id, order_id, currency, "
            "net_seller_earnings_minor, updated_at "
            "FROM marketplace_commercial_settlements "
            "WHERE payout_state = 'eligible' AND payout_ready = 1 "
            "AND blocker_code IS NULL AND updated_at < ? "
            "ORDER BY seller_transaction_id",
            (_marketplace_cutoff(stalled_eligible_hours),),
        )

        total_rows = int(
            (conn.execute(
                "SELECT COUNT(*) AS c FROM marketplace_commercial_settlements"
            ).fetchone() or [0])[0] or 0)
        scanned = _rows(
            "SELECT * FROM marketplace_commercial_settlements "
            "ORDER BY seller_transaction_id LIMIT ?",
            (SNAPSHOT_SCAN_LIMIT,),
        )
    finally:
        conn.close()

    drifting = []
    for row in scanned:
        try:
            drift = commercial.snapshot_drift(row)
        except (KeyError, TypeError, ValueError):
            # A row too malformed to do arithmetic on is itself a finding, and a
            # louder one than being a few cents out.
            drifting.append({"row": row, "drift": None})
            continue
        if any(drift.values()):
            drifting.append({"row": row, "drift": drift})

    # Checked after the arithmetic scan rather than instead of it: a row in a
    # state the state machine does not define is reported, not skipped.
    known_states = set(settlements.PAYOUT_STATES)
    unknown_state = [
        row for row in scanned
        if str(row.get("payout_state") or "") not in known_states
    ]

    incident_ids = []

    def _report(rows, *, code, incident_type, severity, describe):
        for row in rows[:MARKETPLACE_INCIDENT_CAP]:
            tx_id = row.get("seller_transaction_id")
            incident = incidents.open_incident(
                incident_type,
                domain="seller_payments",
                severity=severity,
                summary=describe(row),
                details={
                    "seller_transaction_id": tx_id,
                    "seller_id": row.get("seller_id"),
                    "order_id": row.get("order_id"),
                    "currency": row.get("currency"),
                    "net_seller_earnings_minor": row.get("net_seller_earnings_minor"),
                    "code": code,
                    "occurrences_this_run": len(rows),
                },
                related_object=f"marketplace_settlement:{tx_id}",
                # Keyed on the row and the finding, not on the run, so a stall
                # that persists for a week is one incident that keeps being
                # refreshed rather than a week of duplicates.
                incident_key=f"{incident_type}:marketplace_settlement:{tx_id}:{code}",
            )
            if incident.get("id"):
                incident_ids.append(incident["id"])

    _report(
        unreachable,
        code="payout_scheduled_without_provider_id",
        incident_type=incidents.PAYOUT_STATE_CONFLICT,
        severity="critical",
        describe=lambda r: (
            f"Marketplace settlement {r.get('seller_transaction_id')} has been "
            f"'scheduled' since {r.get('updated_at')} with no provider payout id; "
            "no Stripe webhook can match it, so it will never leave this state."
        ),
    )
    _report(
        hold_end_unknown,
        code="protection_hold_without_end",
        incident_type=incidents.PAYOUT_STATE_CONFLICT,
        severity="critical",
        describe=lambda r: (
            f"Marketplace settlement {r.get('seller_transaction_id')} is in "
            "protection_hold with no protection_ends_at; the hold can never "
            "elapse, so the row can never become eligible."
        ),
    )
    _report(
        hold_elapsed,
        code="protection_hold_not_released",
        incident_type=incidents.PAYOUT_STATE_CONFLICT,
        severity="warning",
        describe=lambda r: (
            f"Marketplace settlement {r.get('seller_transaction_id')} held past "
            f"{r.get('protection_ends_at')} and still not eligible; the "
            "eligibility sweep appears not to be running."
        ),
    )
    _report(
        unscheduled,
        code="eligible_but_never_scheduled",
        incident_type=incidents.PAYOUT_STATE_CONFLICT,
        severity="warning",
        describe=lambda r: (
            f"Marketplace settlement {r.get('seller_transaction_id')} has been "
            f"eligible and unblocked since {r.get('updated_at')} with no payout "
            "scheduled; the payout scheduler appears not to be running."
        ),
    )
    _report(
        unknown_state,
        code="payout_state_not_in_state_machine",
        incident_type=incidents.PAYOUT_STATE_CONFLICT,
        severity="critical",
        describe=lambda r: (
            f"Marketplace settlement {r.get('seller_transaction_id')} carries "
            f"payout_state {r.get('payout_state')!r}, which the settlement state "
            "machine does not define."
        ),
    )

    for finding in drifting[:MARKETPLACE_INCIDENT_CAP]:
        row, drift = finding["row"], finding["drift"]
        tx_id = row.get("seller_transaction_id")
        worst = max(abs(v) for v in drift.values()) if drift else CRITICAL_DRIFT_CENTS
        incident = incidents.open_incident(
            incidents.BALANCE_MISMATCH,
            domain="seller_payments",
            severity=_severity_for_drift(worst),
            summary=(
                f"Marketplace settlement {tx_id} does not satisfy "
                "net == gross - reversed"
                + (f" (off by {drift})" if drift else " and could not be read")
            ),
            details={
                "seller_transaction_id": tx_id,
                "seller_id": row.get("seller_id"),
                "order_id": row.get("order_id"),
                "currency": row.get("currency"),
                "drift_minor": drift,
                "code": "commercial_snapshot_mismatch",
                "occurrences_this_run": len(drifting),
            },
            related_object=f"marketplace_settlement:{tx_id}",
            incident_key=(
                f"{incidents.BALANCE_MISMATCH}:marketplace_settlement:{tx_id}"
            ),
        )
        if incident.get("id"):
            incident_ids.append(incident["id"])

    return {
        "tables_missing": False,
        "settlements_total": total_rows,
        "settlements_scanned": len(scanned),
        # True means the arithmetic invariant was checked on only part of the
        # table. Named rather than inferred from the two counts, because a caller
        # reading a clean report should not have to do that subtraction to learn
        # the report is partial.
        "scan_truncated": total_rows > len(scanned),
        "scheduled_without_provider_id": len(unreachable),
        "protection_hold_without_end": len(hold_end_unknown),
        "protection_hold_not_released": len(hold_elapsed),
        "eligible_but_never_scheduled": len(unscheduled),
        "payout_state_unknown": len(unknown_state),
        "snapshot_mismatches": len(drifting),
        "incidents": incident_ids,
    }


# ---------------------------------------------------------------------------
# Orchestration + run history
# ---------------------------------------------------------------------------

def open_critical_incidents(domain: Optional[str] = None) -> int:
    """How many critical financial incidents are open right now.

    ``acknowledged`` counts as open: someone has seen it, nobody has fixed it,
    and the money is still wherever it was. Only ``resolved`` and ``ignored``
    stop counting, and both of those require a written note.
    """
    incidents.ensure_schema()
    sql = ("SELECT COUNT(*) FROM financial_incidents "
           "WHERE severity = 'critical' AND status IN ('open', 'acknowledged')")
    params: tuple = ()
    if domain:
        sql += " AND domain = ?"
        params = (domain,)
    conn = db.connect()
    try:
        row = conn.execute(sql, params).fetchone()
    finally:
        conn.close()
    return int((row or [0])[0] or 0)


def run_all() -> dict:
    """Run every pure-local check once and persist the run summary.

    Individual check failures are recorded in the summary (and as
    RECONCILIATION_FAILURE incidents) rather than aborting the sweep — one
    broken subsystem must not blind the others.
    """
    ensure_schema()
    incidents.ensure_schema()
    started_at = _utc_now_iso()
    checks = {}
    total_incidents = 0
    errors = 0
    for name, fn in (
        ("ledger_balances", reconcile_ledger_balances),
        ("ad_wallets", reconcile_ad_wallets),
        ("funding_sessions", reconcile_funding_sessions),
        ("webhook_inbox", reconcile_webhook_inbox),
        ("suspense", reconcile_suspense),
        ("seller_payouts", reconcile_seller_payouts),
        ("rewards", reconcile_rewards),
        ("marketplace_settlements", reconcile_marketplace_settlements),
    ):
        try:
            result = fn()
            checks[name] = result
            total_incidents += len(result.get("incidents") or [])
        except Exception as exc:  # noqa: BLE001 — one check must not blind the rest
            errors += 1
            checks[name] = {"error": str(exc)[:500]}
            try:
                incidents.open_incident(
                    incidents.RECONCILIATION_FAILURE,
                    domain="ledger",
                    severity="critical",
                    summary=f"Reconciliation check '{name}' raised: {str(exc)[:200]}",
                    details={"check": name, "error": str(exc)[:500]},
                    related_object=f"reconciliation_check:{name}",
                    incident_key=f"{incidents.RECONCILIATION_FAILURE}:check:{name}",
                )
            except Exception:
                pass

    finished_at = _utc_now_iso()
    summary = {
        "started_at": started_at,
        "finished_at": finished_at,
        "checks": checks,
        "incidents_opened_or_refreshed": total_incidents,
        "check_errors": errors,
        # Standing state, not this run's output. A run that opens nothing while
        # yesterday's critical is still open is not a clean run, and an alerting
        # caller that reads only `incidents_opened_or_refreshed` would call it
        # one. Counted after the sweep so it includes anything just opened.
        "open_critical_incidents": open_critical_incidents(),
    }
    conn = db.connect()
    try:
        conn.execute(
            "INSERT INTO reconciliation_runs (started_at, finished_at, summary_json) "
            "VALUES (?, ?, ?)",
            (started_at, finished_at, json.dumps(summary)),
        )
        conn.commit()
    finally:
        conn.close()
    return summary


def last_run() -> Optional[dict]:
    """The most recent persisted run summary, or None if never run."""
    ensure_schema()
    conn = db.connect()
    try:
        cur = conn.execute(
            "SELECT id, started_at, finished_at, summary_json "
            "FROM reconciliation_runs ORDER BY id DESC LIMIT 1"
        )
        row = _row_to_dict(cur.fetchone())
    finally:
        conn.close()
    if row is None:
        return None
    summary = {}
    try:
        summary = json.loads(row.get("summary_json") or "{}")
    except (TypeError, ValueError):
        summary = {}
    return {
        "run_id": row.get("id"),
        "started_at": row.get("started_at"),
        "finished_at": row.get("finished_at"),
        "summary": summary,
    }
