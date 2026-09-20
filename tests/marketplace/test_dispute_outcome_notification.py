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


def test_the_metadata_gated_branch_is_unreachable_for_a_real_dispute():
    # The branch every test above exercises is keyed on
    # `metadata.seller_transaction_id`, which a real Stripe Dispute never
    # carries. So none of that copy reaches production by this route, and the
    # tests below — which post the object in its real shape — are the ones that
    # say whether a seller is told anything at all.
    _seed_transaction(8208)
    bare = _dispute("dp_bare", 8208, "lost")
    bare["metadata"] = {}
    assert _post_webhook("charge.dispute.closed", bare, event_id="evt_dp_bare").status_code == 200
    assert _notifications(8208, SELLER) == []
    assert _notifications(8208, BUYER) == []


# --------------------------------------------------------------------------
# The route that actually reaches a seller in production.
#
# `emit_marketplace_dispute_notifications` resolves transactions through the
# payment intent, so it works on a Dispute exactly as Stripe sends one. It used
# to return early for `charge.dispute.closed`: the seller was told a payment was
# disputed and then never told how it ended.
# --------------------------------------------------------------------------


def _seed_settled_order(tx_id):
    """A paid order with the settlement row the payment-intent fallback reads.

    `pulse_marketplace_reversal_transaction_ids` looks the dispute up by
    `provider_payment_id` on the settlement, not by the order row, so an order
    without one resolves to no transactions and the whole path is skipped.
    """
    import bot
    from services.business_os.ledger import ledger
    from services import marketplace_settlement_service as settlement
    bot.init_db(); ledger.ensure_schema(); settlement.ensure_schema()
    quote = {"quote_id": f"q{tx_id}", "fee_policy_version": "MARKETPLACE_LEGACY_CURRENT",
             "payout_policy_version": "MARKETPLACE_PAYOUTS_V1", "platform_fee_bps": 1000,
             "merchandise_net_minor": 10000, "shipping_minor": 0, "tax_minor": 0,
             "seller_shipping_credit_minor": 0, "buyer_total_minor": 10000}
    metadata_json = json.dumps({"commercial_quote": quote})
    now = datetime.utcnow().isoformat(timespec="seconds")
    conn = bot.db()
    try:
        conn.execute("DELETE FROM seller_transactions WHERE id=?", (tx_id,))
        conn.execute(
            "INSERT INTO seller_transactions (id, buyer_user_id, seller_user_id, seller_type,"
            " item_type, item_id, amount_cents, currency, platform_fee_cents, seller_net_cents,"
            " status, stripe_payment_intent_id, metadata_json, created_at, updated_at)"
            " VALUES (?, ?, ?, 'merchant', 'marketplace_product', 55, 10000, 'USD', 1000, 9000,"
            " 'paid', ?, ?, ?, ?)",
            (tx_id, BUYER, SELLER, f"pi_note_{tx_id}", metadata_json, now, now))
        conn.commit()
    finally:
        conn.close()
    settlement.settle_paid_transaction(
        {"id": tx_id, "seller_user_id": SELLER, "item_type": "marketplace_product",
         "amount_cents": 10000, "platform_fee_cents": 1000, "seller_net_cents": 9000,
         "currency": "USD", "metadata_json": metadata_json},
        payout_ready=True, provider_payment_id=f"pi_note_{tx_id}")


def _real_dispute(dispute_id, tx_id, status):
    """A Dispute in the shape Stripe sends one: no seller metadata of any kind."""
    return {"id": dispute_id, "object": "dispute", "charge": f"ch_{dispute_id}",
            "payment_intent": f"pi_note_{tx_id}", "amount": 10000, "currency": "usd",
            "reason": "product_not_received", "status": status, "metadata": {}}


def _dispute_notifications(tx_id, user_id=SELLER):
    import bot
    conn = bot.db(); conn.row_factory = bot.sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(
            "SELECT type, title, body, metadata_json FROM pulse_notifications"
            " WHERE user_id=? AND entity_type='marketplace_dispute' AND entity_id=?"
            " ORDER BY id", (user_id, str(tx_id))).fetchall()]
    finally:
        conn.close()


def _run_dispute_lifecycle(tx_id, dispute_id, status):
    """Open the dispute, then close it — the order production sends them in.

    Closing without opening leaves the settlement with no hold to release, so
    the won path takes its `needs_review` branch and the test would be asserting
    against a state a real chargeback never passes through.
    """
    _seed_settled_order(tx_id)
    opened = _real_dispute(dispute_id, tx_id, "needs_response")
    opened["evidence_details"] = {"due_by": int(time.time()) + 7 * 86400}
    assert _post_webhook("charge.dispute.created", opened,
                         event_id=f"evt_{dispute_id}_open").status_code == 200
    assert _post_webhook("charge.dispute.closed", _real_dispute(dispute_id, tx_id, status),
                         event_id=f"evt_{dispute_id}_close").status_code == 200
    return _dispute_notifications(tx_id)


def test_a_real_lost_chargeback_now_reaches_the_seller():
    # The gap this closes. Before, the seller got the "a payment was disputed"
    # email and then silence, while their earnings were reversed underneath them.
    notes = _run_dispute_lifecycle(8301, "dp_real_lost", "lost")
    assert [n["type"] for n in notes] == ["dispute_opened", "dispute_lost"]
    closed = notes[-1]
    assert "resolved" not in closed["title"].lower()
    assert "buyer" in closed["title"].lower()
    assert "reversed" in closed["body"].lower()


def test_a_real_won_chargeback_is_told_apart_from_a_lost_one():
    won = _run_dispute_lifecycle(8302, "dp_real_won", "won")[-1]
    lost = _run_dispute_lifecycle(8303, "dp_real_lost_2", "lost")[-1]
    assert won["type"] == "dispute_won"
    assert won["title"] != lost["title"]
    assert won["body"] != lost["body"]
    assert "reversed" not in won["body"].lower()


def test_a_real_closed_inquiry_claims_nothing_either_way():
    # `warning_closed` never became a dispute. Both "you won" and "you lost"
    # would be untrue, and the seller has nothing to do about it.
    note = _run_dispute_lifecycle(8304, "dp_real_inquiry", "warning_closed")[-1]
    assert note["type"] == "dispute_inquiry_closed"
    for word in ("favour", "reversed", "won", "lost"):
        assert word not in note["title"].lower(), note["title"]


def test_an_unrecognised_terminal_status_says_nothing():
    # Guessing is the failure this whole change is about. A status the mapping
    # does not know must not fall back to either outcome.
    _seed_settled_order(8305)
    odd = _real_dispute("dp_real_odd", 8305, "under_review")
    assert _post_webhook("charge.dispute.closed", odd,
                         event_id="evt_dp_real_odd").status_code == 200
    assert _dispute_notifications(8305) == []


def test_the_close_queues_its_own_email_rather_than_the_opening_one():
    # The notification carries a template key, not a rendered body, so the wrong
    # key here mails the seller "a payment has been disputed" for its resolution.
    notes = _run_dispute_lifecycle(8306, "dp_real_email", "lost")
    templates = [json.loads(n["metadata_json"] or "{}").get("email_template") for n in notes]
    assert templates == ["dispute_opened", "dispute_lost"]

    from services import payments_notifications
    rendered = payments_notifications.render_email(json.loads(notes[-1]["metadata_json"]))
    assert rendered is not None
    assert "resolved" not in rendered["subject"].lower()
    assert "buyer's favour" in rendered["subject"].lower()


def test_a_stripe_redelivery_of_the_close_does_not_notify_twice():
    # Stripe redelivers on any non-2xx and on its own schedule. Two "your
    # dispute was lost" notifications for one chargeback reads as two losses.
    _run_dispute_lifecycle(8307, "dp_real_replay", "lost")
    assert _post_webhook("charge.dispute.closed", _real_dispute("dp_real_replay", 8307, "lost"),
                         event_id="evt_dp_real_replay_again").status_code == 200
    assert [n["type"] for n in _dispute_notifications(8307)] == ["dispute_opened", "dispute_lost"]
