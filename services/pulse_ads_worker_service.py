"""PulseSoc Ads background worker engine.

One Railway service (`pulsesoc-ads-worker`, entrypoint `pulse_ads_worker.py`)
runs five LOGICAL domains as independent internal handlers sharing this queue:

    operations   campaign scheduling, activation, completion, budget sweeps
    events       queued/retryable ad event side-work (never the financial write)
    attribution  conversion attribution (last-click, windowed, evidence-backed)
    billing      wallet reconciliation against the transaction ledger
    reporting    daily aggregate precomputation for dashboards

Design rules, matching the rest of the ads engine:

* PostgreSQL/SQLite is the financial source of truth. Redis (via
  services.cache_engine) is only an accelerator and is never required.
* Every job carries an idempotency key; enqueueing the same key twice is a
  no-op, and running the same job twice must be safe (handlers are idempotent
  or delegate to idempotent primitives like record_spend_event /
  attribute_purchases).
* Retries use exponential backoff; after max_attempts a job moves to the
  dead-letter state ('dead') instead of looping forever.
* The synchronous delivery/event endpoints stay authoritative for money —
  this worker NEVER charges a wallet for an impression. It reconciles,
  aggregates, attributes, and sweeps.
* Any failure here must never take PulseSoc down: callers enqueue
  best-effort, and the worker logs + retries instead of raising outward.
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from datetime import datetime, timedelta, timezone

from services.pulse_ads_service import (
    clean_json,
    clean_text,
    now_iso,
    row_to_dict,
    safe_int,
)

log = logging.getLogger("pulse_ads_worker")

QUEUES = ("operations", "events", "attribution", "billing", "reporting")

# Backoff: 30s, 60s, 120s, 240s ... capped at 1 hour.
BACKOFF_BASE_SECONDS = int(os.getenv("ADS_WORKER_BACKOFF_BASE_SECONDS", "30"))
BACKOFF_CAP_SECONDS = int(os.getenv("ADS_WORKER_BACKOFF_CAP_SECONDS", "3600"))
DEFAULT_MAX_ATTEMPTS = int(os.getenv("ADS_WORKER_MAX_ATTEMPTS", "5"))


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

def ensure_schema(conn) -> None:
    """Idempotent. Called by the worker on boot and lazily by enqueuers."""
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS pulse_ad_jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            queue TEXT NOT NULL,
            job_type TEXT NOT NULL,
            payload_json TEXT DEFAULT '{}',
            idempotency_key TEXT,
            correlation_id TEXT,
            status TEXT DEFAULT 'pending',
            attempts INTEGER DEFAULT 0,
            max_attempts INTEGER DEFAULT 5,
            run_after TEXT,
            last_error TEXT,
            created_at TEXT,
            updated_at TEXT,
            completed_at TEXT
        )
        """
    )
    for ddl in (
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_pulse_ad_jobs_idem ON pulse_ad_jobs(idempotency_key)",
        "CREATE INDEX IF NOT EXISTS idx_pulse_ad_jobs_claim ON pulse_ad_jobs(status, run_after, queue)",
        "CREATE INDEX IF NOT EXISTS idx_pulse_ad_jobs_queue ON pulse_ad_jobs(queue, status)",
    ):
        try:
            cur.execute(ddl)
        except Exception:
            pass
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS pulse_ad_daily_aggregates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            campaign_id INTEGER NOT NULL,
            creative_id INTEGER DEFAULT 0,
            placement_key TEXT DEFAULT '',
            day TEXT NOT NULL,
            impressions INTEGER DEFAULT 0,
            viewable_impressions INTEGER DEFAULT 0,
            clicks INTEGER DEFAULT 0,
            spend_cents INTEGER DEFAULT 0,
            conversions INTEGER DEFAULT 0,
            revenue_cents INTEGER DEFAULT 0,
            computed_at TEXT
        )
        """
    )
    try:
        cur.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_pulse_ad_daily_agg_key "
            "ON pulse_ad_daily_aggregates(campaign_id, creative_id, placement_key, day)"
        )
    except Exception:
        pass
    conn.commit()


# ---------------------------------------------------------------------------
# Queue primitives
# ---------------------------------------------------------------------------

def enqueue_job(
    conn,
    queue: str,
    job_type: str,
    payload: dict | None = None,
    *,
    idempotency_key: str = "",
    run_after: str = "",
    max_attempts: int = 0,
    correlation_id: str = "",
) -> dict:
    """Deduplicating enqueue. Same idempotency_key → returns the existing job.

    Safe to call from request handlers: any failure is contained by callers
    (ads must never take the feed down), and the write is tiny.
    """
    if queue not in QUEUES:
        raise ValueError(f"Unknown ads queue: {queue}")
    ensure_schema(conn)
    key = clean_text(idempotency_key, 180) or f"{queue}:{job_type}:{uuid.uuid4().hex}"
    cur = conn.cursor()
    cur.execute("SELECT id, status FROM pulse_ad_jobs WHERE idempotency_key=?", (key,))
    existing = row_to_dict(cur.fetchone())
    if existing:
        return {"ok": True, "job_id": existing.get("id"), "deduped": True}
    now = now_iso()
    cur.execute(
        """
        INSERT INTO pulse_ad_jobs
        (queue, job_type, payload_json, idempotency_key, correlation_id, status,
         attempts, max_attempts, run_after, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, 'pending', 0, ?, ?, ?, ?)
        """,
        (
            queue,
            clean_text(job_type, 80),
            clean_json(payload or {}),
            key,
            clean_text(correlation_id, 64) or uuid.uuid4().hex[:16],
            safe_int(max_attempts, DEFAULT_MAX_ATTEMPTS, 1, 20),
            run_after or now,
            now,
            now,
        ),
    )
    job_id = cur.lastrowid
    conn.commit()
    return {"ok": True, "job_id": job_id, "deduped": False}


def _backoff_run_after(attempts: int) -> str:
    delay = min(BACKOFF_CAP_SECONDS, BACKOFF_BASE_SECONDS * (2 ** max(0, attempts - 1)))
    return (datetime.now(timezone.utc) + timedelta(seconds=delay)).isoformat()


def queue_health(conn) -> dict:
    ensure_schema(conn)
    cur = conn.cursor()
    cur.execute("SELECT queue, status, COUNT(*) AS n FROM pulse_ad_jobs GROUP BY queue, status")
    depth: dict = {}
    for row in cur.fetchall():
        r = row_to_dict(row)
        depth.setdefault(r.get("queue"), {})[r.get("status")] = safe_int(r.get("n"), 0)
    cur.execute("SELECT COUNT(*) AS n FROM pulse_ad_jobs WHERE status='dead'")
    dead = safe_int(row_to_dict(cur.fetchone()).get("n"), 0)
    return {"queues": depth, "dead_letter_total": dead}


def recover_orphaned_jobs(conn, stale_minutes: int = 10) -> int:
    """Restart recovery: jobs a killed worker left in 'processing' go back to
    'pending' after a visibility timeout. Handlers are idempotent, so the
    at-least-once redelivery this creates is safe."""
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=max(1, stale_minutes))).isoformat()
    cur = conn.cursor()
    cur.execute(
        "UPDATE pulse_ad_jobs SET status='pending', updated_at=? WHERE status='processing' AND updated_at<?",
        (now_iso(), cutoff),
    )
    recovered = cur.rowcount or 0
    conn.commit()
    if recovered:
        log.warning("ADS_JOBS_RECOVERED orphaned=%s", recovered)
    return recovered


def requeue_dead_job(conn, job_id: int) -> dict:
    """Admin/ops escape hatch: put a dead-lettered job back in play."""
    cur = conn.cursor()
    now = now_iso()
    cur.execute(
        "UPDATE pulse_ad_jobs SET status='pending', attempts=0, last_error='', run_after=?, updated_at=? "
        "WHERE id=? AND status='dead'",
        (now, now, safe_int(job_id, minimum=1)),
    )
    conn.commit()
    return {"ok": True, "requeued": bool(cur.rowcount)}


# ---------------------------------------------------------------------------
# Handlers — every handler is idempotent under re-delivery.
# ---------------------------------------------------------------------------

def _handle_refresh_aggregates(conn, payload: dict) -> dict:
    campaign_id = safe_int(payload.get("campaign_id"), minimum=1)
    day = clean_text(payload.get("day"), 10) or now_iso()[:10]
    return refresh_daily_aggregates(conn, campaign_id, day)


def _handle_attribute_conversions(conn, payload: dict) -> dict:
    from services import pulse_ads_reporting

    return pulse_ads_reporting.attribute_purchases(
        conn,
        account_id=safe_int(payload.get("account_id"), 0),
        campaign_id=safe_int(payload.get("campaign_id"), 0),
    )


def _handle_reconcile_wallet(conn, payload: dict) -> dict:
    return reconcile_wallet(conn, safe_int(payload.get("account_id"), minimum=1))


def _handle_campaign_state_sweep(conn, payload: dict) -> dict:
    return run_operations_cycle(conn)


HANDLERS = {
    "refresh_aggregates": _handle_refresh_aggregates,
    "attribute_conversions": _handle_attribute_conversions,
    "reconcile_wallet": _handle_reconcile_wallet,
    "campaign_state_sweep": _handle_campaign_state_sweep,
}


def process_pending_jobs(conn, batch_size: int = 20) -> dict:
    """Claim-and-run loop body. Malformed jobs dead-letter immediately;
    transient failures back off exponentially until max_attempts."""
    ensure_schema(conn)
    cur = conn.cursor()
    now = now_iso()
    cur.execute(
        """
        SELECT * FROM pulse_ad_jobs
        WHERE status='pending' AND (run_after IS NULL OR run_after<=?)
        ORDER BY id ASC LIMIT ?
        """,
        (now, safe_int(batch_size, 20, 1, 200)),
    )
    jobs = [row_to_dict(row) for row in cur.fetchall()]
    processed = failed = dead = 0
    for job in jobs:
        job_id = job.get("id")
        corr = job.get("correlation_id") or ""
        # Claim guard: only one worker wins the pending→processing transition.
        cur.execute(
            "UPDATE pulse_ad_jobs SET status='processing', attempts=COALESCE(attempts,0)+1, updated_at=? "
            "WHERE id=? AND status='pending'",
            (now_iso(), job_id),
        )
        conn.commit()
        if not cur.rowcount:
            continue
        attempts = safe_int(job.get("attempts"), 0) + 1
        handler = HANDLERS.get(job.get("job_type") or "")
        try:
            if handler is None:
                raise _PermanentJobError(f"unknown job_type {job.get('job_type')!r}")
            try:
                payload = json.loads(job.get("payload_json") or "{}")
                if not isinstance(payload, dict):
                    raise ValueError("payload is not an object")
            except Exception as exc:
                raise _PermanentJobError(f"malformed payload: {exc}")
            result = handler(conn, payload)
            done = now_iso()
            cur.execute(
                "UPDATE pulse_ad_jobs SET status='completed', last_error='', updated_at=?, completed_at=? WHERE id=?",
                (done, done, job_id),
            )
            conn.commit()
            processed += 1
            log.info(
                "ADS_JOB_OK id=%s queue=%s type=%s corr=%s attempts=%s result=%s",
                job_id, job.get("queue"), job.get("job_type"), corr, attempts,
                json.dumps(result, default=str)[:400],
            )
        except _PermanentJobError as exc:
            try:
                conn.rollback()
            except Exception:
                pass
            cur.execute(
                "UPDATE pulse_ad_jobs SET status='dead', last_error=?, updated_at=? WHERE id=?",
                (clean_text(str(exc), 400), now_iso(), job_id),
            )
            conn.commit()
            dead += 1
            log.error("ADS_JOB_DEAD id=%s corr=%s error=%s", job_id, corr, exc)
        except Exception as exc:
            try:
                conn.rollback()
            except Exception:
                pass
            max_attempts = safe_int(job.get("max_attempts"), DEFAULT_MAX_ATTEMPTS, 1, 20)
            if attempts >= max_attempts:
                cur.execute(
                    "UPDATE pulse_ad_jobs SET status='dead', last_error=?, updated_at=? WHERE id=?",
                    (clean_text(str(exc), 400), now_iso(), job_id),
                )
                dead += 1
                log.error("ADS_JOB_DEAD id=%s corr=%s attempts=%s error=%s", job_id, corr, attempts, exc)
            else:
                cur.execute(
                    "UPDATE pulse_ad_jobs SET status='pending', last_error=?, run_after=?, updated_at=? WHERE id=?",
                    (clean_text(str(exc), 400), _backoff_run_after(attempts), now_iso(), job_id),
                )
                failed += 1
                log.warning("ADS_JOB_RETRY id=%s corr=%s attempts=%s error=%s", job_id, corr, attempts, exc)
            conn.commit()
    return {"claimed": len(jobs), "processed": processed, "retried": failed, "dead": dead}


class _PermanentJobError(Exception):
    """Job can never succeed (malformed / unknown) → dead-letter immediately."""


# ---------------------------------------------------------------------------
# Domain: operations
# ---------------------------------------------------------------------------

def run_operations_cycle(conn) -> dict:
    """Scheduling + state sweep. Idempotent: transitions are guarded by the
    exact status they move from, so a repeated run is a no-op."""
    cur = conn.cursor()
    now = now_iso()
    # scheduled → active once start_at has arrived.
    cur.execute(
        "UPDATE pulse_ad_campaigns SET status='active', updated_at=? "
        "WHERE status='scheduled' AND start_at IS NOT NULL AND start_at<=?",
        (now, now),
    )
    activated = cur.rowcount or 0
    # active → completed once end_at has passed.
    cur.execute(
        "UPDATE pulse_ad_campaigns SET status='completed', updated_at=? "
        "WHERE status='active' AND end_at IS NOT NULL AND end_at<>'' AND end_at<?",
        (now, now),
    )
    completed = cur.rowcount or 0
    conn.commit()
    # Safety-net budget sweep: pause active campaigns whose account can no
    # longer fund them (the spend path already does this in-line; the sweep
    # catches accounts drained by refunds/reversals between deliveries).
    paused = 0
    try:
        from services import pulse_ad_payments

        cur.execute(
            "SELECT DISTINCT ad_account_id FROM pulse_ad_campaigns WHERE status='active'"
        )
        account_ids = [safe_int(row_to_dict(r).get("ad_account_id"), 0) for r in cur.fetchall()]
        for account_id in account_ids:
            if not account_id:
                continue
            cur.execute(
                "SELECT business_type FROM pulse_ad_accounts WHERE id=?", (account_id,)
            )
            acct = row_to_dict(cur.fetchone())
            if clean_text((acct or {}).get("business_type"), 80) == "internal_promotion":
                continue
            paused += pulse_ad_payments._pause_campaigns_without_balance(conn, account_id)
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        log.exception("ADS_OPS_BUDGET_SWEEP_FAILED")
    if activated or completed or paused:
        log.info("ADS_OPS_CYCLE activated=%s completed=%s paused=%s", activated, completed, paused)
    return {"activated": activated, "completed": completed, "paused": paused}


# ---------------------------------------------------------------------------
# Domain: billing (reconciliation — never charges)
# ---------------------------------------------------------------------------

def reconcile_wallet(conn, account_id: int) -> dict:
    """Compare the wallet row against the sum of its ledger transactions.
    Mismatches are logged as wallet events for admin review; this function
    never mutates balances (the ledger is append-only evidence)."""
    from services import pulse_ad_payments

    wallet = pulse_ad_payments.ensure_wallet(conn, account_id)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT transaction_type, COALESCE(SUM(amount_cents),0) AS total
        FROM pulse_ad_wallet_transactions
        WHERE account_id=? AND status='posted'
        GROUP BY transaction_type
        """,
        (safe_int(account_id, minimum=1),),
    )
    sums = {row_to_dict(r).get("transaction_type"): safe_int(row_to_dict(r).get("total"), 0) for r in cur.fetchall()}
    # Types per VALID_TRANSACTION_TYPES: credits add money, debits remove it;
    # reserve/release_reserve are soft holds and adjustment has no fixed sign,
    # so all three are excluded from the drift math rather than guessed at.
    credits = sum(v for k, v in sums.items() if k in ("funding", "credit", "promo_credit"))
    debits = sum(v for k, v in sums.items() if k in ("spend", "refund", "chargeback"))
    ledger_net = credits - debits
    wallet_total = (
        safe_int(wallet.get("available_balance_cents"), 0)
        + safe_int(wallet.get("promotional_credits_cents"), 0)
        + safe_int(wallet.get("bonus_credits_cents"), 0)
        + safe_int(wallet.get("refund_credits_cents"), 0)
    )
    drift_cents = wallet_total - ledger_net
    result = {
        "account_id": account_id,
        "wallet_total_cents": wallet_total,
        "ledger_net_cents": ledger_net,
        "drift_cents": drift_cents,
        "balanced": drift_cents == 0,
        "ledger_sums": sums,
    }
    if drift_cents != 0:
        try:
            pulse_ad_payments._wallet_event(
                conn,
                account_id,
                "reconciliation_drift",
                "wallet_ledger_mismatch",
                details=result,
            )
            conn.commit()
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
        log.error("ADS_BILLING_DRIFT account=%s drift_cents=%s", account_id, drift_cents)
    return result


def run_billing_cycle(conn) -> dict:
    cur = conn.cursor()
    cur.execute(
        "SELECT DISTINCT account_id FROM pulse_ad_wallet_transactions "
        "WHERE created_at>=? ",
        ((datetime.now(timezone.utc) - timedelta(days=2)).isoformat(),),
    )
    checked = drifted = 0
    for row in cur.fetchall():
        account_id = safe_int(row_to_dict(row).get("account_id"), 0)
        if not account_id:
            continue
        outcome = reconcile_wallet(conn, account_id)
        checked += 1
        if not outcome.get("balanced"):
            drifted += 1
    return {"accounts_checked": checked, "accounts_drifted": drifted}


# ---------------------------------------------------------------------------
# Domain: attribution
# ---------------------------------------------------------------------------

def run_attribution_cycle(conn) -> dict:
    """attribute_purchases is idempotent (order_ref uniqueness), so running it
    for every account with recent clicks is safe."""
    from services import pulse_ads_reporting

    cur = conn.cursor()
    cur.execute(
        """
        SELECT DISTINCT c.ad_account_id
        FROM pulse_ad_clicks k JOIN pulse_ad_campaigns c ON c.id=k.campaign_id
        WHERE k.created_at>=?
        """,
        ((datetime.now(timezone.utc) - timedelta(days=8)).isoformat(),),
    )
    accounts = [safe_int(row_to_dict(r).get("ad_account_id"), 0) for r in cur.fetchall()]
    total_new = 0
    for account_id in accounts:
        if not account_id:
            continue
        outcome = pulse_ads_reporting.attribute_purchases(conn, account_id=account_id)
        total_new += safe_int(outcome.get("new"), 0)
    return {"accounts": len(accounts), "new_attributions": total_new}


# ---------------------------------------------------------------------------
# Domain: reporting
# ---------------------------------------------------------------------------

def refresh_daily_aggregates(conn, campaign_id: int, day: str) -> dict:
    """Recompute-and-replace: aggregates are derived data, so the idempotent
    strategy is delete + reinsert from the raw rows for that (campaign, day)."""
    ensure_schema(conn)
    campaign_id = safe_int(campaign_id, minimum=1)
    day = clean_text(day, 10)
    # ISO timestamps start with the day prefix, so LIKE 'YYYY-MM-DD%' bounds the day.
    cur = conn.cursor()
    rows: dict = {}

    def bucket(creative_id, placement_key):
        key = (safe_int(creative_id, 0), clean_text(placement_key or "", 80))
        if key not in rows:
            rows[key] = {
                "impressions": 0, "viewable_impressions": 0, "clicks": 0,
                "spend_cents": 0, "conversions": 0, "revenue_cents": 0,
            }
        return rows[key]

    cur.execute(
        "SELECT creative_id, placement_key, COUNT(*) AS n, "
        "SUM(CASE WHEN COALESCE(viewable,0)=1 THEN 1 ELSE 0 END) AS viewable_n "
        "FROM pulse_ad_impressions WHERE campaign_id=? AND created_at LIKE ? GROUP BY creative_id, placement_key",
        (campaign_id, f"{day}%"),
    )
    for r in (row_to_dict(x) for x in cur.fetchall()):
        b = bucket(r.get("creative_id"), r.get("placement_key"))
        b["impressions"] = safe_int(r.get("n"), 0)
        b["viewable_impressions"] = safe_int(r.get("viewable_n"), 0)
    cur.execute(
        "SELECT creative_id, placement_key, COUNT(*) AS n FROM pulse_ad_clicks "
        "WHERE campaign_id=? AND created_at LIKE ? GROUP BY creative_id, placement_key",
        (campaign_id, f"{day}%"),
    )
    for r in (row_to_dict(x) for x in cur.fetchall()):
        bucket(r.get("creative_id"), r.get("placement_key"))["clicks"] = safe_int(r.get("n"), 0)
    cur.execute(
        "SELECT creative_id, COALESCE(SUM(amount_cents),0) AS total FROM pulse_ad_wallet_transactions "
        "WHERE campaign_id=? AND transaction_type='spend' AND status='posted' AND created_at LIKE ? GROUP BY creative_id",
        (campaign_id, f"{day}%"),
    )
    for r in (row_to_dict(x) for x in cur.fetchall()):
        bucket(r.get("creative_id"), "")["spend_cents"] = safe_int(r.get("total"), 0)
    try:
        cur.execute(
            "SELECT creative_id, COUNT(*) AS n, COALESCE(SUM(revenue_cents),0) AS rev FROM pulse_ad_attributions "
            "WHERE campaign_id=? AND created_at LIKE ? GROUP BY creative_id",
            (campaign_id, f"{day}%"),
        )
        for r in (row_to_dict(x) for x in cur.fetchall()):
            b = bucket(r.get("creative_id"), "")
            b["conversions"] = safe_int(r.get("n"), 0)
            b["revenue_cents"] = safe_int(r.get("rev"), 0)
    except Exception:
        pass  # attributions table may not exist yet on a fresh deployment
    cur.execute(
        "DELETE FROM pulse_ad_daily_aggregates WHERE campaign_id=? AND day=?",
        (campaign_id, day),
    )
    now = now_iso()
    for (creative_id, placement_key), b in rows.items():
        cur.execute(
            """
            INSERT INTO pulse_ad_daily_aggregates
            (campaign_id, creative_id, placement_key, day, impressions, viewable_impressions,
             clicks, spend_cents, conversions, revenue_cents, computed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                campaign_id, creative_id, placement_key, day,
                b["impressions"], b["viewable_impressions"], b["clicks"],
                b["spend_cents"], b["conversions"], b["revenue_cents"], now,
            ),
        )
    conn.commit()
    return {"campaign_id": campaign_id, "day": day, "rows": len(rows)}


def run_reporting_cycle(conn, days_back: int = 1) -> dict:
    """Refresh aggregates for campaigns with recent delivery."""
    cur = conn.cursor()
    since = (datetime.now(timezone.utc) - timedelta(days=days_back + 1)).isoformat()
    cur.execute(
        "SELECT DISTINCT campaign_id FROM pulse_ad_impressions WHERE created_at>=?",
        (since,),
    )
    campaign_ids = [safe_int(row_to_dict(r).get("campaign_id"), 0) for r in cur.fetchall()]
    today = datetime.now(timezone.utc).date()
    refreshed = 0
    for campaign_id in campaign_ids:
        if not campaign_id:
            continue
        for offset in range(days_back + 1):
            day = (today - timedelta(days=offset)).isoformat()
            refresh_daily_aggregates(conn, campaign_id, day)
            refreshed += 1
    return {"campaigns": len(campaign_ids), "days_refreshed": refreshed}
