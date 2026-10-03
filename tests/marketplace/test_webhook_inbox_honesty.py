"""A webhook the inbox failed to record must not be answered "OK".

`record_webhook_event` inserts the signature-verified event into
`payment_webhook_events`, whose `provider_event_id` is UNIQUE. It wrapped that
INSERT in a blanket `except Exception` that returned ``{"duplicate": True}``,
and the handler answers a duplicate with 200.

So two opposite situations produced the same answer:

  * Stripe re-sent an event we already hold. Skipping is correct.
  * The write failed for a reason that has nothing to do with uniqueness -- a
    pool timeout (8+8 connections, 3s timeout), a dropped socket, a failover,
    a missing table. Nothing was recorded.

In the second case the 200 tells Stripe the event was accepted, so it is never
redelivered, and a signature-verified money event is lost permanently with no
row, no retry and no alert. It is the most reassuring possible rendering of
"we dropped your payment".

The fix distinguishes them by evidence rather than by driver exception class:
after the rollback, look for the row. Present means somebody committed it and
the skip is justified; absent means the identity was never recorded and the
event must be retried, which is a 5xx.

These tests drive the real endpoint with really-signed payloads. Calling
`record_webhook_event` alone would prove the service returns a new dict while
the handler quietly went on answering 200.
"""

import hashlib
import hmac
import json
import os
import sqlite3
import sys
import tempfile
import time
from datetime import datetime

os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(
    tempfile.mkdtemp(prefix="webhook_inbox_honesty_"), "test.db")
# Throwaway local secrets, so the tests post properly signed events rather than
# reaching past signature verification. A test key is used deliberately: these
# tests must never be able to move real money.
os.environ.setdefault("STRIPE_WEBHOOK_SECRET", "whsec_webhook_inbox_honesty_tests_only")
os.environ.setdefault("STRIPE_SECRET_KEY", "sk_test_webhook_inbox_honesty_tests_only")
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

BUYER = 8601
SELLER = 8602
WEBHOOK_PATH = "/api/stripe/webhook"


# --------------------------------------------------------------------------
# Driving the real endpoint
# --------------------------------------------------------------------------

def _sign_and_post(event):
    """Post `event` at the real webhook endpoint, correctly signed."""
    import bot
    bot.init_db()
    payload = json.dumps(event, separators=(",", ":")).encode("utf-8")
    timestamp = str(int(time.time()))
    digest = hmac.new(os.environ["STRIPE_WEBHOOK_SECRET"].encode("utf-8"),
                      f"{timestamp}.".encode("utf-8") + payload, hashlib.sha256).hexdigest()
    return bot.webhook_app.test_client().post(
        WEBHOOK_PATH, data=payload,
        headers={"Stripe-Signature": f"t={timestamp},v1={digest}",
                 "Content-Type": "application/json"})


def _event(event_id, event_type="customer.subscription.updated", obj=None):
    return {"id": event_id, "object": "event", "type": event_type, "livemode": False,
            "data": {"object": obj if obj is not None else {"id": "sub_inbox_honesty"}}}


def _inbox_rows(provider_event_id=None):
    import bot
    conn = bot.db()
    conn.row_factory = sqlite3.Row
    try:
        if provider_event_id is None:
            rows = conn.execute("SELECT * FROM payment_webhook_events ORDER BY id").fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM payment_webhook_events WHERE provider_event_id=? ORDER BY id",
                (provider_event_id,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _clear_inbox():
    import bot
    bot.init_db()
    conn = bot.db()
    try:
        conn.execute("DELETE FROM payment_webhook_events")
        conn.execute("DELETE FROM stripe_events")
        conn.commit()
    finally:
        conn.close()


def _seed_unpaid_order(tx_id, session_id):
    """An unpaid Marketplace order for a completed-checkout event to settle.

    Present so the "nothing below ran" claim is a measurement rather than an
    inference from the `return` statement.
    """
    import bot
    now = datetime.utcnow().isoformat(timespec="seconds")
    conn = bot.db()
    try:
        conn.execute("DELETE FROM seller_transactions WHERE id=?", (tx_id,))
        conn.execute(
            "INSERT INTO seller_transactions (id, buyer_user_id, seller_user_id, seller_type,"
            " item_type, item_id, amount_cents, currency, platform_fee_cents, seller_net_cents,"
            " status, stripe_checkout_session_id, metadata_json, created_at, updated_at)"
            " VALUES (?, ?, ?, 'merchant', 'marketplace_product', 77, 9498, 'USD', 0, 9498,"
            " 'pending', ?, ?, ?, ?)",
            (tx_id, BUYER, SELLER, session_id,
             json.dumps({"cart_checkout": "1", "seller_transaction_ids": [tx_id]}), now, now))
        conn.commit()
    finally:
        conn.close()


def _order_status(tx_id):
    import bot
    conn = bot.db()
    try:
        row = conn.execute("SELECT status FROM seller_transactions WHERE id=?", (tx_id,)).fetchone()
        return row[0] if row else ""
    finally:
        conn.close()


# --------------------------------------------------------------------------
# Simulating a transient write failure
# --------------------------------------------------------------------------
#
# Only the *transient* failure is simulated. A real unique violation is never
# faked: the duplicate tests below get theirs from the real UNIQUE constraint
# on provider_event_id, because a faked constraint would only prove that the
# fake behaves the way it was written.

class _FailingCursor:
    def __init__(self, real, *, fail_select):
        self._real = real
        self._fail_select = fail_select

    def execute(self, sql, params=()):
        head = sql.strip().upper()
        if head.startswith("INSERT"):
            # An sqlite3.OperationalError is the right shape: transient, and
            # emphatically not an IntegrityError on a unique index.
            raise sqlite3.OperationalError("database is locked")
        if self._fail_select and head.startswith("SELECT"):
            raise sqlite3.OperationalError("database is locked")
        return self._real.execute(sql, params)

    def fetchone(self):
        return self._real.fetchone()

    def fetchall(self):
        return self._real.fetchall()

    @property
    def lastrowid(self):
        return self._real.lastrowid


class _FailingWriteConnection:
    """Real reads, failing writes.

    The table is present and the SELECT genuinely runs against it, so when the
    service looks for the row it gets the truth: absent means absent.
    """

    def __init__(self, real, *, fail_select=False):
        self._real = real
        self._fail_select = fail_select

    def cursor(self):
        return _FailingCursor(self._real.cursor(), fail_select=self._fail_select)

    def commit(self):
        return self._real.commit()

    def rollback(self):
        return self._real.rollback()

    def close(self):
        return self._real.close()


class inbox_write_failing:
    """Make the inbox INSERT fail for the duration of the block.

    Patches `creator_economy_service.db_service` rather than `services.db`
    itself, so the blast radius is the one function under test.
    """

    def __init__(self, *, fail_select=False):
        self.fail_select = fail_select

    def __enter__(self):
        from services import creator_economy_service as ces
        from services import db as real_db
        self._ces = ces
        self._saved = ces.db_service
        fail_select = self.fail_select

        class _Shim:
            @staticmethod
            def connect():
                conn = real_db.connect()
                try:
                    conn.row_factory = sqlite3.Row
                except Exception:
                    pass
                return _FailingWriteConnection(conn, fail_select=fail_select)

        ces.db_service = _Shim
        return self

    def __exit__(self, *exc):
        self._ces.db_service = self._saved
        return False


# --------------------------------------------------------------------------
# The duplicate case must keep working -- against the real constraint
# --------------------------------------------------------------------------

def test_a_genuinely_redelivered_event_is_still_skipped():
    """The fix must not turn deduplication off.

    This is the half that was already right, and the half a careless fix breaks
    by answering 500 to everything that raises.
    """
    _clear_inbox()
    first = _sign_and_post(_event("evt_inbox_dup_1"))
    assert first.status_code == 200, first.data
    second = _sign_and_post(_event("evt_inbox_dup_1"))
    assert second.status_code == 200, second.data
    rows = _inbox_rows("evt_inbox_dup_1")
    assert len(rows) == 1, f"the UNIQUE constraint should hold one row, got {rows}"


def test_the_unique_constraint_the_fix_depends_on_actually_exists():
    """The whole design rests on the database refusing the second insert.

    If `provider_event_id` were not UNIQUE, duplicates would insert cleanly,
    the except branch would never run, and replay protection would be an
    if-statement rather than an invariant (§26).
    """
    import bot
    bot.init_db()
    conn = bot.db()
    try:
        indexes = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type IN ('index','table')"
            " AND tbl_name='payment_webhook_events'").fetchall()
    finally:
        conn.close()
    blob = " ".join(str(r[0] or "") for r in indexes).lower()
    assert "unique" in blob, f"no uniqueness on payment_webhook_events: {blob}"

    _clear_inbox()
    conn = bot.db()
    try:
        conn.execute(
            "INSERT INTO payment_webhook_events (provider_event_id, event_type, processed_at,"
            " status, error, raw_json) VALUES ('evt_dup_proof','t','','received','','{}')")
        conn.commit()
        raised = False
        try:
            conn.execute(
                "INSERT INTO payment_webhook_events (provider_event_id, event_type, processed_at,"
                " status, error, raw_json) VALUES ('evt_dup_proof','t','','received','','{}')")
            conn.commit()
        except sqlite3.IntegrityError:
            raised = True
        assert raised, "a second row with the same provider_event_id was accepted"
    finally:
        conn.rollback()
        conn.close()


# --------------------------------------------------------------------------
# The transient failure -- the defect
# --------------------------------------------------------------------------

def test_an_unrecorded_event_is_not_answered_ok():
    """The defect. A failed write used to answer 200 and lose the event."""
    _clear_inbox()
    with inbox_write_failing():
        response = _sign_and_post(_event("evt_inbox_transient"))
    assert response.status_code >= 500, (
        "a webhook the inbox could not record was answered "
        f"{response.status_code}; Stripe will never redeliver it")


def test_the_premise_of_the_500_holds_nothing_was_recorded():
    """A 5xx is only honest if the identity really is absent.

    If a row had landed, the right answer would be 200 -- so the 500 and the
    empty inbox have to be asserted together, or the test would pass on a
    version that answers 500 while also recording the event.
    """
    _clear_inbox()
    with inbox_write_failing():
        response = _sign_and_post(_event("evt_inbox_premise"))
    assert response.status_code >= 500
    assert _inbox_rows("evt_inbox_premise") == []


def test_an_unrecorded_event_does_not_settle_the_order():
    """Nothing below the inbox may run without a dedupe record.

    Processing an event whose identity was not recorded, and then being
    redelivered it, double-applies the handler. Measured on a real unpaid
    order rather than inferred from the `return`.
    """
    _clear_inbox()
    _seed_unpaid_order(8701, "cs_inbox_unsettled")
    assert _order_status(8701) == "pending"
    with inbox_write_failing():
        response = _sign_and_post(_event(
            "evt_inbox_unsettled", "checkout.session.completed",
            {"id": "cs_inbox_unsettled", "payment_status": "paid",
             "amount_total": 9498, "currency": "usd",
             "metadata": {"cart_checkout": "1", "seller_transaction_ids": "8701"}}))
    assert response.status_code >= 500
    assert _order_status(8701) == "pending", (
        "the order was settled from an event the inbox never recorded, so a "
        "redelivery of the same event would settle it twice")


def test_an_unreadable_inbox_fails_closed():
    """When the read-back also fails, there is no evidence of a duplicate.

    Absence of proof is not proof of a duplicate, so the only safe answer is
    the one that keeps Stripe retrying.
    """
    _clear_inbox()
    with inbox_write_failing(fail_select=True):
        response = _sign_and_post(_event("evt_inbox_unreadable"))
    assert response.status_code >= 500, response.data


def test_a_write_failure_over_an_existing_row_is_still_a_duplicate():
    """The subtle half: the fix must not become "500 on anything that raises".

    Same event id, same failing INSERT -- but the row is already there, so the
    event genuinely is a duplicate and 200 is correct. This is what makes the
    fix evidence-based rather than a blanket inversion of the old bug.
    """
    _clear_inbox()
    first = _sign_and_post(_event("evt_inbox_both"))
    assert first.status_code == 200
    assert len(_inbox_rows("evt_inbox_both")) == 1
    with inbox_write_failing():
        response = _sign_and_post(_event("evt_inbox_both"))
    assert response.status_code == 200, (
        "a real duplicate was answered "
        f"{response.status_code}; the fix over-corrected into refusing replays")


def test_the_service_returns_three_distinguishable_outcomes():
    """Read directly, because the handler can only see what the service says.

    `ok`/`duplicate` have to be three distinct shapes. Collapsing any two is
    exactly the defect, in either direction.
    """
    from services import creator_economy_service as ces
    _clear_inbox()

    fresh = ces.record_webhook_event("evt_shape_fresh", "t", {"id": "evt_shape_fresh"})
    assert fresh.get("ok") is True and fresh.get("duplicate") is False, fresh

    repeat = ces.record_webhook_event("evt_shape_fresh", "t", {"id": "evt_shape_fresh"})
    assert repeat.get("ok") is True and repeat.get("duplicate") is True, repeat

    with inbox_write_failing():
        broken = ces.record_webhook_event("evt_shape_broken", "t", {"id": "evt_shape_broken"})
    assert broken.get("ok") is False, broken
    assert broken.get("duplicate") is False, (
        f"a failed write still reports itself as a duplicate: {broken}")

    assert len({(fresh.get("ok"), fresh.get("duplicate")),
                (repeat.get("ok"), repeat.get("duplicate")),
                (broken.get("ok"), broken.get("duplicate"))}) == 3


# --------------------------------------------------------------------------
# The id-less event -- a silent collapse, not a loud failure
# --------------------------------------------------------------------------

def test_an_event_with_no_id_is_refused():
    """`event.get("id", "")` yields "" rather than NULL, and "" is UNIQUE.

    So the first id-less event is recorded and every later one is reported as a
    duplicate of it and answered 200 -- discarded silently. Refusing is louder
    and costs nothing: Stripe always sends an id.
    """
    _clear_inbox()
    response = _sign_and_post(_event(""))
    assert response.status_code == 400, response.data
    assert _inbox_rows() == [], "an id-less event reached the inbox"


def test_two_id_less_events_do_not_collapse_onto_one_row():
    """The collapse itself. Two different events, one shared empty identity.

    Without the guard the second is indistinguishable from a replay of the
    first, which is how one event can silently stand in for another.
    """
    _clear_inbox()
    first = _sign_and_post(_event("", "charge.succeeded", {"id": "ch_one", "amount": 1000}))
    second = _sign_and_post(_event("", "charge.succeeded", {"id": "ch_two", "amount": 250000}))
    assert first.status_code == 400 and second.status_code == 400
    assert _inbox_rows() == [], (
        "an id-less event was recorded, so the next one will be read as its replay")


def test_a_missing_id_key_is_refused_too():
    """Absent and blank must both be refused; only one of them is `""`."""
    _clear_inbox()
    event = _event("x")
    event.pop("id")
    response = _sign_and_post(event)
    assert response.status_code == 400, response.data
    assert _inbox_rows() == []


def test_the_id_guard_runs_before_anything_touches_the_database():
    """Ordering. A guard after the first write would not prevent the collapse."""
    import inspect
    import bot
    source = inspect.getsource(bot.stripe_webhook)
    guard = source.find("STRIPE_WEBHOOK_EVENT_HAS_NO_ID")
    assert guard != -1, "the id guard is gone"
    for writer in ("record_webhook_event", "enqueue_event", "record_stripe_event"):
        position = source.find(writer)
        assert position == -1 or position > guard, (
            f"{writer} can run before the event id is validated")
