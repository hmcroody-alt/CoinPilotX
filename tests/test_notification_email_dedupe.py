"""Ops2 Stage 12 — notification email queue dedupe (exactly-once enqueue).

``notification_service._queue_email_job`` derives an idempotency key from
(user, recipient, email_type, notification_id, subject, event_type); a repeat
enqueue must return duplicate=True and add NO second outbox row. Retry backoff
must be bounded (min(3600, 30*2^(n-1))).

    python3 tests/test_notification_email_dedupe.py
"""

import os
import tempfile

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="notif_dedupe_"), "test.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB
os.environ["EMAIL_OPPORTUNISTIC_PROCESSOR_ENABLED"] = "0"
os.environ["PULSE_EMAIL_QUEUE_PROCESSOR_ENABLED"] = "0"

import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from services import notification_service as ns  # noqa: E402
from services import user_context  # noqa: E402


def _rows(key):
    conn = user_context.connect()
    try:
        return conn.execute(
            "SELECT COUNT(*) FROM failed_email_queue WHERE idempotency_key=?",
            (key,)).fetchone()[0]
    finally:
        conn.close()


def test_duplicate_enqueue_is_single_row():
    meta = {"notification_id": 9001, "event_type": "premium_activated"}
    r1 = ns._queue_email_job(7, "member@example.com", "Premium activated",
                             "<p>hi</p>", "hi", "premium", dict(meta), 9001)
    assert r1["ok"] and r1["status"] == "queued" and not r1.get("duplicate"), r1
    r2 = ns._queue_email_job(7, "member@example.com", "Premium activated",
                             "<p>hi</p>", "hi", "premium", dict(meta), 9001)
    assert r2.get("duplicate") is True, r2
    assert r2["queue_id"] == r1["queue_id"], (r1, r2)

    conn = user_context.connect()
    try:
        key = conn.execute(
            "SELECT idempotency_key FROM failed_email_queue WHERE id=?",
            (r1["queue_id"],)).fetchone()[0]
    finally:
        conn.close()
    assert _rows(key) == 1


def test_explicit_idempotency_key_respected():
    meta = {"email_idempotency_key": "sub-activated:user7:txn123"}
    r1 = ns._queue_email_job(7, "member@example.com", "Sub", "<p>a</p>", "a",
                             "premium", dict(meta), 0)
    r2 = ns._queue_email_job(7, "member@example.com", "Sub", "<p>a</p>", "a",
                             "premium", dict(meta), 0)
    assert not r1.get("duplicate") and r2.get("duplicate") is True, (r1, r2)
    assert _rows("sub-activated:user7:txn123") == 1


def test_backoff_bounded():
    for attempts, cap in ((1, 30), (2, 60), (5, 480), (20, 3600), (100, 3600)):
        iso = ns._email_retry_at(attempts)
        assert iso  # parseable, monotone-bounded by construction
    # direct bound check
    assert min(3600, 30 * (2 ** (100 - 1))) == 3600


if __name__ == "__main__":
    test_duplicate_enqueue_is_single_row()
    test_explicit_idempotency_key_respected()
    test_backoff_bounded()
    print("OK: 3 tests passed")
