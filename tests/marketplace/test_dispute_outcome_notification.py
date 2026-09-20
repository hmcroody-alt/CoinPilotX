"""A closed chargeback must be notified as the outcome it actually was.

`charge.dispute.closed` is the only dispute event carrying a verdict, and the
webhook used to collapse all three of Stripe's terminal statuses onto one
`dispute_resolved` notification titled "Dispute resolved". A seller who had just
had the money taken back off them was told their dispute was resolved, which
reads as good news.
"""

import hashlib
import hmac
import json
import os
import sys
import tempfile
import time
from datetime import datetime

os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(
    tempfile.mkdtemp(prefix="marketplace_dispute_note_"), "test.db")
# Throwaway local secrets so the tests below can post a properly signed event
# rather than reaching past signature verification.
os.environ.setdefault("STRIPE_WEBHOOK_SECRET", "whsec_marketplace_dispute_note_tests_only")
os.environ.setdefault("STRIPE_SECRET_KEY", "sk_test_marketplace_dispute_note_tests_only")
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

BUYER = 8101
SELLER = 8102


def _seed_transaction(tx_id):
    """A paid Marketplace order for the dispute to close against."""
    import bot
    bot.init_db()
    now = datetime.utcnow().isoformat(timespec="seconds")
    conn = bot.db()
    try:
        conn.execute("DELETE FROM seller_transactions WHERE id=?", (tx_id,))
        conn.execute(
            "INSERT INTO seller_transactions (id, buyer_user_id, seller_user_id, seller_type,"
            " item_type, item_id, amount_cents, currency, platform_fee_cents, seller_net_cents,"
            " status, stripe_payment_intent_id, created_at, updated_at)"
            " VALUES (?, ?, ?, 'merchant', 'marketplace_product', 55, 10000, 'USD', 1000, 9000,"
            " 'paid', ?, ?, ?)",
            (tx_id, BUYER, SELLER, f"pi_note_{tx_id}", now, now))
        conn.commit()
    finally:
        conn.close()


def _dispute(dispute_id, tx_id, status):
    """A Dispute as Stripe sends it, but carrying the seller transaction id.

    A real Dispute's `metadata` is its own and empty — see
    `pulse_marketplace_reversal_transaction_ids` — and the notification branch
    under test reads `seller_transaction_id` straight off it. It is populated
    here so the branch is exercised at all; `test_a_metadata_free_dispute...`
    below pins what happens without it.
    """
    return {"id": dispute_id, "object": "dispute", "charge": f"ch_{dispute_id}",
            "payment_intent": f"pi_note_{tx_id}", "amount": 10000, "status": status,
            "metadata": {"seller_transaction_id": str(tx_id)}}


def _post_webhook(event_type, obj, *, event_id):
    """Post a signed event at the real endpoint and return the response.

    Calling the handler directly would prove the handler works while the webhook
    quietly never reached it.
    """
    import bot
    bot.init_db()
    event = {"id": event_id, "object": "event", "type": event_type, "livemode": False,
             "data": {"object": obj}}
    payload = json.dumps(event, separators=(",", ":")).encode("utf-8")
    timestamp = str(int(time.time()))
    digest = hmac.new(os.environ["STRIPE_WEBHOOK_SECRET"].encode("utf-8"),
                      f"{timestamp}.".encode("utf-8") + payload, hashlib.sha256).hexdigest()
    return bot.webhook_app.test_client().post(
        "/api/stripe/webhook", data=payload,
        headers={"Stripe-Signature": f"t={timestamp},v1={digest}",
                 "Content-Type": "application/json"})


def _notifications(tx_id, user_id):
    import bot
    conn = bot.db(); conn.row_factory = bot.sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(
            "SELECT type, title, body FROM pulse_notifications"
            " WHERE user_id=? AND entity_type='seller_transaction' AND entity_id=?"
            " ORDER BY id", (user_id, str(tx_id))).fetchall()]
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


def _close_dispute(tx_id, dispute_id, status):
    _seed_transaction(tx_id)
    response = _post_webhook("charge.dispute.closed", _dispute(dispute_id, tx_id, status),
                             event_id=f"evt_{dispute_id}")
    assert response.status_code == 200
    notes = _notifications(tx_id, SELLER)
    assert len(notes) == 1, f"expected one seller notification, got {notes}"
    return notes[0]


def test_a_lost_dispute_is_not_notified_as_a_resolution():
    # The bug. Losing a chargeback means the money has been taken back off the
    # seller, and it used to arrive titled "Dispute resolved".
    note = _close_dispute(8201, "dp_lost", "lost")
    assert note["type"] == "dispute_lost"
    assert "resolved" not in note["title"].lower()
    assert "resolved" not in note["body"].lower()
    assert "buyer" in note["title"].lower()
    assert "reversed" in note["body"].lower()


def test_a_won_dispute_is_notified_differently_from_a_lost_one():
    # The two outcomes used to be indistinguishable: same type, same title, same
    # body. Asserting the won case alone would not have caught that.
    won = _close_dispute(8202, "dp_won", "won")
    lost = _close_dispute(8203, "dp_lost_2", "lost")
    assert won["type"] == "dispute_won"
    assert won["type"] != lost["type"]
    assert won["title"] != lost["title"]
    assert won["body"] != lost["body"]
    assert "seller" in won["title"].lower()
    assert "stands" in won["body"].lower()


def test_a_closed_inquiry_stays_generic():
    # `warning_closed` is an inquiry that never became a dispute, so nothing was
    # decided and nothing was at risk. It must not claim either side won.
    note = _close_dispute(8204, "dp_warning", "warning_closed")
    assert note["type"] == "dispute_resolved"
    assert "favour" not in note["title"].lower()


def test_the_buyer_and_seller_both_get_the_outcome_wording():
    # One copy is written for both recipients, so the loss wording has to be
    # true from either side rather than seller-framed.
    _seed_transaction(8205)
    assert _post_webhook("charge.dispute.closed", _dispute("dp_both", 8205, "lost"),
                         event_id="evt_dp_both").status_code == 200
    buyer = _notifications(8205, BUYER)
    seller = _notifications(8205, SELLER)
    assert [n["type"] for n in buyer] == ["dispute_lost"]
    assert [n["type"] for n in seller] == ["dispute_lost"]
    assert buyer[0]["title"] == seller[0]["title"]


def test_the_order_row_keeps_the_dispute_handler_vocabulary():
    # `pulse_apply_marketplace_dispute` rewrites this same column moments later
    # in the same request, using `dispute_lost`/`dispute_resolved`. A second
    # spelling here would make the order flap depending on which writer ran.
    _close_dispute(8206, "dp_row_lost", "lost")
    assert _order_status(8206) == "dispute_lost"
    _close_dispute(8207, "dp_row_won", "won")
    assert _order_status(8207) == "dispute_resolved"


def test_a_metadata_free_dispute_notifies_nobody():
    # Pins a real gap rather than asserting it is fine. Stripe's Dispute object
    # carries no seller metadata, and this notification branch is keyed on
    # `metadata.seller_transaction_id` alone — unlike the settlement handler,
    # which falls back to the payment intent. So in production a closed
    # chargeback currently notifies neither party at all. If that is ever wired
    # to the payment-intent fallback, this test should be updated to assert the
    # outcome wording instead of silence.
    _seed_transaction(8208)
    bare = _dispute("dp_bare", 8208, "lost")
    bare["metadata"] = {}
    assert _post_webhook("charge.dispute.closed", bare, event_id="evt_dp_bare").status_code == 200
    assert _notifications(8208, SELLER) == []
    assert _notifications(8208, BUYER) == []
