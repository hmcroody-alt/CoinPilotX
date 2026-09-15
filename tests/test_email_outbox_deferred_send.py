"""The outbox as a durable timer, and the veto that runs before every send.

`failed_email_queue` is not only a retry buffer. Both processors honour
`next_retry_at <= now`, so a row written with a far-future value is a
scheduled send — which is how meeting reminders are delivered years out
without a second scheduler existing.

Three things have to hold for that to be true, and each has a test here:

1. A future row is not sent early, and is sent once it comes due.
2. Nothing drags a future row forward. `retry_failed_email_queue` did exactly
   that: an operator clicking "Retry failed emails" reset *every* pending
   row's `next_retry_at` to now, which would have fired a 2032 reminder that
   afternoon. It now only touches rows that are already due.
3. A claimed row is re-validated before the provider is called, and a
   validator that cannot answer refuses.
"""

from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime, timedelta

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="pulsesoc_outbox_"), "test.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB
os.environ["EMAIL_OPPORTUNISTIC_PROCESSOR_ENABLED"] = "0"

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, _ROOT)

import pytest  # noqa: E402

from services import email_send_guard  # noqa: E402
from services import notification_service as ns  # noqa: E402
from services import user_context  # noqa: E402


def _iso(moment: datetime) -> str:
    return moment.replace(microsecond=0).isoformat()


def _reset() -> None:
    conn = user_context.connect()
    cur = conn.cursor()
    ns._ensure_failed_email_queue(cur)
    cur.execute("DELETE FROM failed_email_queue")
    conn.commit()
    conn.close()


def _seed(*, send_after: str = "", email_type: str = "transactional",
          key: str = "") -> int:
    conn = user_context.connect()
    cur = conn.cursor()
    ns._ensure_failed_email_queue(cur)
    now = _iso(datetime.utcnow())
    cur.execute(
        """INSERT INTO failed_email_queue
        (user_id, recipient_email, email_type, subject, html_body, text_body,
         metadata, status, retry_count, max_attempts, last_error,
         next_retry_at, trace_id, idempotency_key, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, '{}', 'pending', 0, 5, '', ?, '', ?, ?, ?)""",
        (7, "someone@example.com", email_type, "Subject", "<p>Body</p>", "Body",
         send_after, key or f"k-{now}-{email_type}-{send_after}", now, now))
    queue_id = int(cur.lastrowid or 0)
    conn.commit()
    conn.close()
    return queue_id


def _row(queue_id: int) -> dict:
    conn = user_context.connect()
    conn.row_factory = __import__("sqlite3").Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM failed_email_queue WHERE id=?", (queue_id,))
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else {}


@pytest.fixture(autouse=True)
def clean_queue():
    _reset()
    yield
    _reset()


def _collector():
    sent = []

    def provider(to_email, subject, body, **kwargs):
        sent.append({"to": to_email, "subject": subject, **kwargs})
        return {"ok": True, "status_code": 202, "message_id": "m1"}

    return sent, provider


# --- 1. the queue is a timer -------------------------------------------------


def test_a_far_future_row_is_not_sent_today():
    queue_id = _seed(send_after=_iso(datetime(2035, 7, 4, 12, 0)))
    sent, provider = _collector()
    result = ns.process_queued_email_notifications(provider_send=provider)
    assert sent == []
    assert result["attempted"] == 0
    assert _row(queue_id)["status"] == "pending"


def test_the_same_row_sends_once_it_comes_due():
    queue_id = _seed(send_after=_iso(datetime.utcnow() - timedelta(minutes=1)))
    sent, provider = _collector()
    ns.process_queued_email_notifications(provider_send=provider)
    assert len(sent) == 1
    assert _row(queue_id)["status"] == "sent"


def test_a_row_with_no_send_after_is_due_immediately():
    """Existing mail is unaffected by any of this."""
    _seed(send_after="")
    sent, provider = _collector()
    ns.process_queued_email_notifications(provider_send=provider)
    assert len(sent) == 1


def test_queueing_with_send_after_writes_it_to_next_retry_at(monkeypatch):
    monkeypatch.setattr(ns, "schedule_email_queue_processing",
                        lambda reason="": {"ok": True})
    result = ns._queue_email_job(
        7, "someone@example.com", "Subject", "<p>Body</p>",
        send_after="2035-07-03T15:00:00")
    assert result["send_after"] == "2035-07-03T15:00:00"
    assert _row(result["queue_id"])["next_retry_at"] == "2035-07-03T15:00:00"


# --- 2. nothing drags a future row forward -----------------------------------


def test_the_manual_retry_does_not_pull_a_future_row_forward():
    """The bug: clicking "Retry failed emails" would have sent every scheduled
    reminder in the table at once, years early."""
    import bot

    future = _iso(datetime(2032, 3, 1, 9, 0))
    scheduled = _seed(send_after=future)
    stuck = _seed(send_after=_iso(datetime.utcnow() - timedelta(hours=2)))

    bot.retry_failed_email_queue(limit=50)

    assert _row(scheduled)["next_retry_at"] == future
    assert _row(scheduled)["status"] == "pending"
    assert _row(stuck)["status"] == "retry_ready"


def test_the_manual_retry_still_rescues_a_due_row():
    import bot

    stuck = _seed(send_after="")
    bot.retry_failed_email_queue(limit=50)
    assert _row(stuck)["status"] == "retry_ready"


# --- 2b. an outage postpones, it does not lose -------------------------------


def _outage():
    """A provider that is down the way providers are actually down."""
    def provider(to_email, subject, body, **kwargs):
        raise ConnectionError("brevo unreachable")

    return provider


def test_a_provider_outage_leaves_the_row_for_the_next_pass():
    """The mutation: a failed send that closes the row.

    A reminder is not a newsletter. If the provider is down for the ninety
    seconds around a 15-minute reminder, the row has to still be there
    afterwards, because there is no upstream that will ever re-create it.
    """
    queue_id = _seed(send_after=_iso(datetime.utcnow() - timedelta(minutes=1)))
    result = ns.process_queued_email_notifications(provider_send=_outage())
    row = _row(queue_id)
    assert result["retry"] == 1
    assert row["status"] == "retry_ready"
    assert row["next_retry_at"] != ""
    assert row["processed_at"] == ""


def test_the_postponed_row_sends_when_the_provider_returns():
    queue_id = _seed(send_after=_iso(datetime.utcnow() - timedelta(minutes=1)))
    ns.process_queued_email_notifications(provider_send=_outage())
    # The backoff is the only thing holding it; step over it the way time does.
    conn = user_context.connect()
    conn.execute("UPDATE failed_email_queue SET next_retry_at='' WHERE id=?",
                 (queue_id,))
    conn.commit()
    conn.close()
    sent, provider = _collector()
    ns.process_queued_email_notifications(provider_send=provider)
    assert len(sent) == 1
    assert _row(queue_id)["status"] == "sent"


def test_an_outage_does_not_burn_the_whole_attempt_budget_at_once():
    """One pass costs one attempt. A row that spent all five in a single
    processor tick would dead-letter during any outage longer than a tick."""
    queue_id = _seed(send_after=_iso(datetime.utcnow() - timedelta(minutes=1)))
    ns.process_queued_email_notifications(provider_send=_outage())
    assert int(_row(queue_id)["retry_count"]) == 1


def test_a_persistent_outage_eventually_dead_letters_rather_than_spinning():
    """Durable is not infinite. The row must reach a terminal state someone
    can count, or a permanently bad address becomes permanent work."""
    queue_id = _seed(send_after=_iso(datetime.utcnow() - timedelta(minutes=1)))
    for _ in range(5):
        conn = user_context.connect()
        conn.execute("UPDATE failed_email_queue SET next_retry_at='' WHERE id=?",
                     (queue_id,))
        conn.commit()
        conn.close()
        ns.process_queued_email_notifications(provider_send=_outage())
    row = _row(queue_id)
    assert row["status"] == "dead_letter"
    assert row["last_error"]


# --- 3. the send-time veto ---------------------------------------------------


def test_an_unguarded_type_is_sent_unchanged():
    _seed(email_type="welcome")
    sent, provider = _collector()
    ns.process_queued_email_notifications(provider_send=provider)
    assert len(sent) == 1


def test_a_refusing_validator_stops_the_send_and_marks_the_row():
    queue_id = _seed(email_type="guarded_type")
    email_send_guard.register("guarded_type", lambda row: (False, "meeting_cancelled"))
    try:
        sent, provider = _collector()
        result = ns.process_queued_email_notifications(provider_send=provider)
    finally:
        email_send_guard.unregister("guarded_type")
    assert sent == []
    assert result["skipped"] == 1
    row = _row(queue_id)
    assert row["status"] == "skipped"
    assert row["last_error"] == "meeting_cancelled"


def test_a_skipped_row_is_terminal_and_not_retried():
    """It must leave 'processing' or the claim strands it, and it must not get
    a next_retry_at or the next pass asks the same dead question forever."""
    queue_id = _seed(email_type="guarded_type")
    email_send_guard.register("guarded_type", lambda row: False)
    try:
        ns.process_queued_email_notifications(provider_send=_collector()[1])
        assert _row(queue_id)["next_retry_at"] == ""
        sent, provider = _collector()
        second = ns.process_queued_email_notifications(provider_send=provider)
    finally:
        email_send_guard.unregister("guarded_type")
    assert second["attempted"] == 0
    assert sent == []


def test_a_validator_that_raises_refuses_the_send():
    """Fail closed. A database blip during a partial deploy is exactly when
    the wrong email is most likely, and exactly when a "send on error" guard
    would evaporate."""
    queue_id = _seed(email_type="guarded_type")
    email_send_guard.register("guarded_type", lambda row: 1 / 0)
    try:
        sent, provider = _collector()
        ns.process_queued_email_notifications(provider_send=provider)
    finally:
        email_send_guard.unregister("guarded_type")
    assert sent == []
    assert _row(queue_id)["last_error"] == "validator_error"


def test_the_guard_sees_the_row_it_is_judging():
    seen = []
    _seed(email_type="guarded_type", key="inspect-me")
    email_send_guard.register("guarded_type",
                              lambda row: (seen.append(row), False)[1])
    try:
        ns.process_queued_email_notifications(provider_send=_collector()[1])
    finally:
        email_send_guard.unregister("guarded_type")
    assert seen and seen[0]["idempotency_key"] == "inspect-me"
    assert seen[0]["recipient_email"] == "someone@example.com"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
