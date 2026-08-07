"""Regression checks for the atomic delivery-job claim.

The duplicate-push bug: process_delivery_jobs dispatched straight from its
SELECT with no claim, so concurrent drains (one Timer thread per gunicorn
worker plus the admin route) sent the same push job twice at the same
timestamp. These tests pin the fix: a job is dispatched exactly once, a job
already claimed by another worker is skipped, and a job stranded in
'processing' by a crashed worker is requeued rather than lost.
"""

from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime, timedelta

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="pulsesoc_job_claim_"), "test.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB
os.environ["PULSESOC_NOTIFICATION_DELIVERY_AUTOPROCESS_ENABLED"] = "0"

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, _ROOT)

from services import db  # noqa: E402
from services import pulsesoc_notification_system as ns  # noqa: E402

_USER_ID = 91


def _seed_job(status: str = "queued", updated_at: str | None = None) -> int:
    conn = db.connect()
    ns.ensure_schema(conn)
    cur = conn.cursor()
    now = ns.now_iso()
    cur.execute(
        "INSERT INTO notifications (recipient_user_id, user_id, type, category, priority, title, body, dedupe_key, created_at, updated_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?)",
        (_USER_ID, _USER_ID, "message", "messages", "high", "Test", "Body", "claim-test-" + status + (updated_at or ""), now, now),
    )
    notification_id = int(cur.lastrowid or 0)
    cur.execute(
        "INSERT INTO notification_delivery_jobs (notification_id, user_id, recipient_user_id, channel, provider, status, dedupe_key, created_at, updated_at)"
        " VALUES (?,?,?,?,?,?,?,?,?)",
        (notification_id, _USER_ID, _USER_ID, "push", "push_router", status, f"job-{status}-{updated_at or 'fresh'}-{notification_id}", now, updated_at or now),
    )
    job_id = int(cur.lastrowid or 0)
    conn.commit()
    conn.close()
    return job_id


def _job_status(job_id: int) -> str:
    conn = db.connect()
    cur = conn.cursor()
    cur.execute("SELECT status FROM notification_delivery_jobs WHERE id=?", (job_id,))
    row = cur.fetchone()
    conn.close()
    return str(row[0] if row else "")


def _run_with_counted_dispatch():
    calls = {"count": 0}
    original = ns._dispatch_job

    def _counting(cur, job, notification, prefs):
        calls["count"] += 1
        return {"ok": True, "status": "sent", "provider": "push_router"}

    ns._dispatch_job = _counting
    try:
        ns.process_delivery_jobs(channels=["push"])
    finally:
        ns._dispatch_job = original
    return calls["count"]


def test_queued_job_dispatched_exactly_once():
    job_id = _seed_job("queued")
    assert _run_with_counted_dispatch() == 1
    assert _job_status(job_id) == "sent"
    # A second drain must not re-send the finished job.
    assert _run_with_counted_dispatch() == 0


def test_job_claimed_by_another_worker_is_skipped():
    job_id = _seed_job("processing")  # fresh updated_at: another worker owns it
    assert _run_with_counted_dispatch() == 0
    assert _job_status(job_id) == "processing"


def test_stale_processing_job_is_requeued_and_sent_once():
    stale = (datetime.utcnow() - timedelta(minutes=30)).replace(microsecond=0).isoformat() + "Z"
    job_id = _seed_job("processing", updated_at=stale)
    assert _run_with_counted_dispatch() == 1
    assert _job_status(job_id) == "sent"


if __name__ == "__main__":
    test_queued_job_dispatched_exactly_once()
    test_job_claimed_by_another_worker_is_skipped()
    test_stale_processing_job_is_requeued_and_sent_once()
    print("OK")
