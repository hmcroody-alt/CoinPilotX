"""Connect account state and connected-account payouts, as the webhook sees them.

Two defects in the Stripe webhook handler, both of which only lose data once
real sellers exist — which is why they were fixed while the platform still has
zero connected accounts:

1. ``account.updated`` wrote the seller's capability state with a bare
   ``UPDATE ... WHERE connected_account_id = ?``. Stripe delivers that event for
   a freshly created account before — or racing with — the onboarding route's
   own insert, so the UPDATE matched zero rows and threw the state away in
   silence. It is an upsert now, but an upsert is only safe if it refuses to
   invent a seller, because Stripe also delivers ``account.updated`` for
   accounts this platform does not own.

2. ``payout.paid`` / ``payout.failed`` resolved the seller from the payout
   object's ``destination``. PulseSoc pays out *as* the connected account
   (``stripe.Payout.create(..., stripe_account=acct)``), and for such a payout
   ``destination`` is the seller's bank account (``ba_...``). The connected
   account id only ever arrives on the event envelope. No seller was resolved,
   so no payout notification was ever sent.

These drive the real endpoint with a real signature and assert on rows and on
captured notification calls — not on source text. A handler can be perfect and
still never be reached, and both of these live deep inside a 900-line branch
chain, so the route is the only honest entry point.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import sys
import tempfile
import time
import unittest
from unittest import mock

os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(
    tempfile.mkdtemp(prefix="connect_webhook_state_"), "test.db")
# Throwaway local values, never real ones, so the events below can be posted
# properly signed instead of reaching past signature verification.
os.environ.setdefault("STRIPE_WEBHOOK_SECRET", "whsec_connect_webhook_state_tests_only")
os.environ.setdefault("STRIPE_SECRET_KEY", "sk_test_connect_webhook_state_tests_only")

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

import bot  # noqa: E402


class CapturingEmit:
    """Captures the call, never sends. The delivery engine has its own suite."""

    def __init__(self):
        self.calls = []

    def __call__(self, event, user_id, context=None, email_only=False):
        self.calls.append({
            "event": event,
            "user_id": int(user_id or 0),
            "context": dict(context or {}),
        })
        return {"ok": True}

    def events(self):
        return [call["event"] for call in self.calls]

    def for_event(self, event):
        return [call for call in self.calls if call["event"] == event]


def _post_webhook(event_type, obj, *, event_id, account=None):
    """Post a signed event at the real endpoint and return the response."""
    bot.init_db()
    event = {"id": event_id, "object": "event", "type": event_type,
             "livemode": False, "data": {"object": obj}}
    if account:
        event["account"] = account
    payload = json.dumps(event, separators=(",", ":")).encode("utf-8")
    timestamp = str(int(time.time()))
    secret = os.environ["STRIPE_WEBHOOK_SECRET"]
    digest = hmac.new(secret.encode("utf-8"),
                      f"{timestamp}.".encode("utf-8") + payload,
                      hashlib.sha256).hexdigest()
    return bot.webhook_app.test_client().post(
        "/api/stripe/webhook", data=payload,
        headers={"Stripe-Signature": f"t={timestamp},v1={digest}",
                 "Content-Type": "application/json"})


def _account_event(account_id, *, user_id=None, seller_type=None,
                   charges=True, payouts=False, currently_due=("document",),
                   disabled_reason=""):
    metadata = {}
    if user_id is not None:
        metadata["user_id"] = str(user_id)
    if seller_type is not None:
        metadata["seller_type"] = seller_type
    return {
        "id": account_id,
        "object": "account",
        "charges_enabled": bool(charges),
        "payouts_enabled": bool(payouts),
        "details_submitted": True,
        "metadata": metadata,
        "requirements": {
            "currently_due": list(currently_due),
            "disabled_reason": disabled_reason,
        },
    }


def _rows_for_account(account_id):
    conn = bot.db()
    conn.row_factory = bot.sqlite3.Row
    try:
        return [dict(row) for row in conn.execute(
            "SELECT * FROM seller_payout_accounts WHERE connected_account_id=?",
            (account_id,)).fetchall()]
    finally:
        conn.close()


def _account_table_size():
    conn = bot.db()
    try:
        return int(conn.execute(
            "SELECT COUNT(*) FROM seller_payout_accounts").fetchone()[0])
    finally:
        conn.close()


def _seed_account(account_id, user_id, seller_type="merchant"):
    now = bot.datetime.utcnow().isoformat(timespec="seconds")
    conn = bot.db()
    try:
        conn.execute(
            "DELETE FROM seller_payout_accounts WHERE connected_account_id=? OR user_id=?",
            (account_id, user_id))
        conn.execute(
            "INSERT INTO seller_payout_accounts (user_id, seller_type, provider,"
            " connected_account_id, onboarding_status, payouts_enabled, charges_enabled,"
            " missing_requirements_json, created_at, updated_at)"
            " VALUES (?, ?, 'stripe', ?, 'complete', 1, 1, '[]', ?, ?)",
            (user_id, seller_type, account_id, now, now))
        conn.commit()
    finally:
        conn.close()


class WebhookTestCase(unittest.TestCase):
    def setUp(self):
        bot.init_db()
        self.emit = CapturingEmit()
        patch = mock.patch.object(bot, "emit_payment_notification", self.emit)
        patch.start()
        self.addCleanup(patch.stop)


# ---------------------------------------------------------------------------
# account.updated — the write that used to match zero rows
# ---------------------------------------------------------------------------

class AccountUpdatedProjection(WebhookTestCase):

    def test_an_account_updated_before_the_row_exists_creates_it(self):
        """The race the old bare UPDATE lost.

        Stripe emits ``account.updated`` for an Express account the moment it is
        created, which can beat the onboarding route's own insert. With an
        UPDATE this state went nowhere and the seller stayed at
        ``not_started`` forever with nothing in the logs to say why.
        """
        account_id = "acct_early_arrival"
        user_id = 87101
        conn = bot.db()
        try:
            conn.execute("DELETE FROM seller_payout_accounts WHERE connected_account_id=? OR user_id=?",
                         (account_id, user_id))
            conn.commit()
        finally:
            conn.close()
        self.assertEqual([], _rows_for_account(account_id))

        response = _post_webhook(
            "account.updated",
            _account_event(account_id, user_id=user_id, seller_type="merchant",
                           charges=True, payouts=False,
                           currently_due=("individual.verification.document",)),
            event_id="evt_account_early_1")

        self.assertEqual(200, response.status_code)
        rows = _rows_for_account(account_id)
        self.assertEqual(1, len(rows), "the early event still wrote nothing")
        row = rows[0]
        self.assertEqual(user_id, int(row["user_id"]))
        self.assertEqual("merchant", row["seller_type"])
        self.assertEqual("requirements_due", row["onboarding_status"])
        self.assertEqual(1, int(row["charges_enabled"]))
        self.assertEqual(0, int(row["payouts_enabled"]))
        self.assertEqual(["individual.verification.document"],
                         json.loads(row["missing_requirements_json"]))

    def test_redelivery_keeps_exactly_one_row_and_does_not_corrupt_it(self):
        """Two deliveries, one row.

        Delivered under two *different* event ids on purpose. An identical id is
        stopped by the handler's own duplicate guard long before it reaches this
        write, so it would prove the guard and nothing about the upsert. Stripe
        sends many distinct ``account.updated`` events for one account; that is
        the case the ON CONFLICT clause has to survive, and an INSERT without
        it would raise or leave a second, contradictory row.
        """
        account_id = "acct_redelivered"
        user_id = 87102
        conn = bot.db()
        try:
            conn.execute("DELETE FROM seller_payout_accounts WHERE connected_account_id=? OR user_id=?",
                         (account_id, user_id))
            conn.commit()
        finally:
            conn.close()

        first = _post_webhook(
            "account.updated",
            _account_event(account_id, user_id=user_id, seller_type="merchant",
                           charges=True, payouts=False,
                           currently_due=("individual.id_number",)),
            event_id="evt_account_dupe_1")
        self.assertEqual(200, first.status_code)
        self.assertEqual(1, len(_rows_for_account(account_id)))

        # The same event id again: the handler's duplicate guard answers this
        # one, and it must remain harmless.
        replay = _post_webhook(
            "account.updated",
            _account_event(account_id, user_id=user_id, seller_type="merchant",
                           charges=True, payouts=False),
            event_id="evt_account_dupe_1")
        self.assertEqual(200, replay.status_code)
        self.assertEqual(1, len(_rows_for_account(account_id)))

        # A genuinely new event for the same account. This is the one that
        # reaches the write a second time.
        second = _post_webhook(
            "account.updated",
            _account_event(account_id, user_id=user_id, seller_type="merchant",
                           charges=True, payouts=False,
                           currently_due=("company.tax_id",)),
            event_id="evt_account_dupe_2")
        self.assertEqual(200, second.status_code)

        rows = _rows_for_account(account_id)
        self.assertEqual(1, len(rows), "the second delivery added a row")
        row = rows[0]
        self.assertEqual(user_id, int(row["user_id"]))
        self.assertEqual("merchant", row["seller_type"])
        self.assertEqual(account_id, row["connected_account_id"])
        self.assertEqual("requirements_due", row["onboarding_status"])
        # The latest event's words, not a merge of both.
        self.assertEqual(["company.tax_id"],
                         json.loads(row["missing_requirements_json"]))

    def test_an_unattributable_account_writes_nothing_and_invents_no_seller(self):
        """Fail closed.

        Stripe delivers ``account.updated`` for accounts this platform has never
        heard of. There is no user to hang such an account on, and a row under a
        null, zero or guessed user_id would be worse than the silent no-op the
        upsert replaced — it would be a fabricated seller that later code would
        treat as real. The orphan is recorded by the projection applier's own
        incident path; the legacy table stays untouched.
        """
        account_id = "acct_not_ours"
        conn = bot.db()
        try:
            conn.execute("DELETE FROM seller_payout_accounts WHERE connected_account_id=?",
                         (account_id,))
            conn.commit()
        finally:
            conn.close()
        before = _account_table_size()

        response = _post_webhook(
            "account.updated",
            # No metadata at all: not created by us, and no row to find.
            _account_event(account_id, charges=True, payouts=True,
                           currently_due=()),
            event_id="evt_account_orphan_1")

        # Never a retry storm: Stripe must be told the event was accepted.
        self.assertEqual(200, response.status_code)
        self.assertEqual([], _rows_for_account(account_id))
        self.assertEqual(before, _account_table_size(),
                         "an unattributable account added a row somewhere")
        # And specifically: no row was hung on a null or zero user.
        conn = bot.db()
        try:
            fabricated = int(conn.execute(
                "SELECT COUNT(*) FROM seller_payout_accounts"
                " WHERE user_id IS NULL OR user_id=0").fetchone()[0])
        finally:
            conn.close()
        self.assertEqual(0, fabricated)


# ---------------------------------------------------------------------------
# payout.paid / payout.failed — resolving the seller from the envelope
# ---------------------------------------------------------------------------

def _payout_object(payout_id, *, destination, amount=5000, failure=""):
    return {
        "id": payout_id,
        "object": "payout",
        "amount": amount,
        "currency": "usd",
        # What Stripe really puts here for a payout created *as* the connected
        # account: the seller's bank account, not the account itself.
        "destination": destination,
        "arrival_date": 1789000000,
        "failure_message": failure,
        "status": "failed" if failure else "paid",
    }


class ConnectedAccountPayouts(WebhookTestCase):

    def test_a_paid_payout_resolves_the_seller_from_the_envelope(self):
        account_id = "acct_payout_paid"
        user_id = 87104
        _seed_account(account_id, user_id)

        response = _post_webhook(
            "payout.paid",
            _payout_object("po_paid_1", destination="ba_1LxSellerBank", amount=5000),
            event_id="evt_payout_paid_1",
            account=account_id)

        self.assertEqual(200, response.status_code)
        paid = self.emit.for_event("payout_paid")
        self.assertEqual(1, len(paid), f"emitted {self.emit.events()}")
        self.assertEqual(user_id, paid[0]["user_id"])
        self.assertEqual(5000, paid[0]["context"]["amount_cents"])
        self.assertEqual("USD", paid[0]["context"]["currency"])
        self.assertEqual("po_paid_1", paid[0]["context"]["payout_id"])

        conn = bot.db()
        conn.row_factory = bot.sqlite3.Row
        try:
            ledger_rows = [dict(r) for r in conn.execute(
                "SELECT * FROM seller_payouts WHERE provider_payout_id=?",
                ("po_paid_1",)).fetchall()]
        finally:
            conn.close()
        self.assertEqual(1, len(ledger_rows))
        self.assertEqual(user_id, int(ledger_rows[0]["user_id"]))
        self.assertEqual("paid", ledger_rows[0]["status"])

    def test_a_failed_payout_resolves_the_seller_from_the_envelope(self):
        account_id = "acct_payout_failed"
        user_id = 87105
        _seed_account(account_id, user_id)

        response = _post_webhook(
            "payout.failed",
            _payout_object("po_failed_1", destination="ba_1LxSellerBank",
                           amount=2500, failure="account_closed"),
            event_id="evt_payout_failed_1",
            account=account_id)

        self.assertEqual(200, response.status_code)
        failed = self.emit.for_event("payout_failed")
        self.assertEqual(1, len(failed), f"emitted {self.emit.events()}")
        self.assertEqual(user_id, failed[0]["user_id"])
        self.assertEqual(2500, failed[0]["context"]["amount_cents"])
        self.assertEqual("account_closed", failed[0]["context"]["failure_reason"])
        self.assertTrue(failed[0]["context"]["failed_at"])

    def test_resolution_does_not_depend_on_destination_being_an_account_id(self):
        """The regression proof, with its own negative control.

        Three claims, because "it works now" is not the same as "the old lookup
        would have failed":

        * no ``seller_payout_accounts`` row is reachable by the ``ba_...`` id,
          so a ``WHERE connected_account_id = destination`` lookup really does
          find nothing here;
        * with the envelope present the seller is still resolved;
        * with the envelope *absent* and the same ``ba_...`` destination,
          nothing is emitted. That last one is what makes the first two mean
          something: it shows the envelope field is doing the work, and it is
          exactly the shape the handler was in before the fix.
        """
        account_id = "acct_regression"
        user_id = 87106
        bank_account_id = "ba_1LxNotAnAccount"
        _seed_account(account_id, user_id)

        # The old key finds nothing. If this ever stops being true the rest of
        # the test proves nothing.
        self.assertEqual([], _rows_for_account(bank_account_id))

        with_envelope = _post_webhook(
            "payout.paid",
            _payout_object("po_regression_1", destination=bank_account_id),
            event_id="evt_payout_regression_1",
            account=account_id)
        self.assertEqual(200, with_envelope.status_code)
        self.assertEqual(1, len(self.emit.for_event("payout_paid")))
        self.assertEqual(user_id, self.emit.for_event("payout_paid")[0]["user_id"])

        # Negative control: same object, no envelope account. This is the
        # information the handler used to have, and it is not enough.
        self.emit.calls.clear()
        without_envelope = _post_webhook(
            "payout.paid",
            _payout_object("po_regression_2", destination=bank_account_id),
            event_id="evt_payout_regression_2")
        self.assertEqual(200, without_envelope.status_code)
        self.assertEqual([], self.emit.for_event("payout_paid"),
                         "a ba_ destination alone resolved a seller")

    def test_a_platform_scoped_payout_still_resolves_from_the_object(self):
        """The fallback the envelope must not have displaced.

        A payout made on the platform account carries no envelope ``account``,
        and its object-level fields are all there is. The fix reorders the
        sources; it must not remove any.
        """
        account_id = "acct_platform_scoped"
        user_id = 87107
        _seed_account(account_id, user_id)

        response = _post_webhook(
            "payout.paid",
            _payout_object("po_platform_1", destination=account_id, amount=1234),
            event_id="evt_payout_platform_1")

        self.assertEqual(200, response.status_code)
        paid = self.emit.for_event("payout_paid")
        self.assertEqual(1, len(paid), f"emitted {self.emit.events()}")
        self.assertEqual(user_id, paid[0]["user_id"])
        self.assertEqual(1234, paid[0]["context"]["amount_cents"])


if __name__ == "__main__":
    unittest.main()
