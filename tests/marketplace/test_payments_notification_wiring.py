"""What the webhook actually hands the notification layer.

The orchestrator's own suite proves the mapping and the rendering. This one
proves the seam above it: that a Stripe object reaching ``bot.py`` resolves the
right *person*, the right *amount* and the right *event*, because that is the
part no amount of template testing can see.

Every emit is captured rather than sent — these assert on the call, not on the
delivery engine, which has its own suite and its own database.
"""

from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import bot  # noqa: E402


class CapturingEmit:
    def __init__(self):
        self.calls = []

    def __call__(self, event, user_id, context=None, email_only=False):
        self.calls.append({
            "event": event,
            "user_id": int(user_id or 0),
            "context": dict(context or {}),
            "email_only": bool(email_only),
        })
        return {"ok": True}

    def events(self):
        return [call["event"] for call in self.calls]

    def for_event(self, event):
        return [call for call in self.calls if call["event"] == event]


BUYER = 4101
SELLER = 4102

TRANSACTION = {
    "id": 9001,
    "buyer_user_id": BUYER,
    "seller_user_id": SELLER,
    "amount_cents": 4250,
    "seller_net_cents": 4038,
    "currency": "usd",
    "metadata_json": '{"title": "Vintage 50mm lens"}',
}


class WiringTestCase(unittest.TestCase):
    def setUp(self):
        self.emit = CapturingEmit()
        patches = [
            mock.patch.object(bot, "emit_payment_notification", self.emit),
            mock.patch.object(bot, "marketplace_transaction_parties",
                              lambda tx_ids: {9001: dict(TRANSACTION)}),
            mock.patch.object(bot, "seller_first_names",
                              lambda user_ids: {BUYER: "Grace", SELLER: "Ada"}),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)


class RefundWiring(WiringTestCase):
    def test_the_buyer_is_told_and_the_seller_is_not(self):
        bot.emit_marketplace_refund_notifications(
            [{"settlement": {"seller_transaction_id": 9001}, "total_refund_minor": 4250}],
            {"id": "ch_1", "created": 1789000000},
        )
        self.assertEqual(["refund_completed"], self.emit.events())
        call = self.emit.calls[0]
        self.assertEqual(BUYER, call["user_id"], "a refund email addressed to the seller is a leak")
        self.assertEqual(4250, call["context"]["amount_cents"])
        self.assertEqual("Grace", call["context"]["buyer_first_name"])

    def test_each_order_is_told_its_own_share_not_the_whole_charge(self):
        # One charge can back several orders. Telling every buyer the charge's
        # `amount_refunded` would overstate what each is actually getting back,
        # which is a false statement about money in writing.
        with mock.patch.object(bot, "marketplace_transaction_parties", lambda tx_ids: {
            9001: dict(TRANSACTION),
            9002: dict(TRANSACTION, id=9002, buyer_user_id=BUYER),
        }):
            bot.emit_marketplace_refund_notifications(
                [
                    {"settlement": {"seller_transaction_id": 9001}, "total_refund_minor": 1000},
                    {"settlement": {"seller_transaction_id": 9002}, "total_refund_minor": 3250},
                ],
                {"id": "ch_1", "created": 1789000000},
            )
        amounts = sorted(call["context"]["amount_cents"] for call in self.emit.calls)
        self.assertEqual([1000, 3250], amounts)

    def test_a_replayed_allocation_sends_nothing(self):
        bot.emit_marketplace_refund_notifications(
            [{"settlement": {"seller_transaction_id": 9001}, "total_refund_minor": 4250,
              "duplicate": True}],
            {"id": "ch_1"},
        )
        self.assertEqual([], self.emit.calls, "a duplicate allocation must not re-email the buyer")

    def test_a_zero_allocation_sends_nothing(self):
        bot.emit_marketplace_refund_notifications(
            [{"settlement": {"seller_transaction_id": 9001}, "total_refund_minor": 0}], {})
        self.assertEqual([], self.emit.calls)


class DisputeWiring(WiringTestCase):
    DISPUTE = {
        "id": "dp_1",
        "amount": 4250,
        "currency": "usd",
        "reason": "product_not_received",
        "status": "needs_response",
        "evidence_details": {"due_by": 1789600000},
    }

    def test_the_seller_is_told_and_the_buyer_is_not(self):
        bot.emit_marketplace_dispute_notifications([9001], self.DISPUTE, "charge.dispute.created")
        self.assertEqual(["dispute_opened"], self.emit.events())
        call = self.emit.calls[0]
        self.assertEqual(SELLER, call["user_id"], "the buyer must not be emailed the seller's dispute")
        self.assertEqual("dp_1", call["context"]["dispute_id"])
        self.assertEqual("product not received", call["context"]["dispute_reason"])
        self.assertTrue(call["context"]["evidence_due_by"], "a deadline the seller cannot see is not a deadline")

    def test_creation_does_not_also_send_the_evidence_email(self):
        # Stripe's `charge.dispute.created` already carries `due_by`, so the
        # naive reading fires both templates in the same second.
        bot.emit_marketplace_dispute_notifications([9001], self.DISPUTE, "charge.dispute.created")
        self.assertNotIn("dispute_action_required", self.emit.events())

    def test_a_later_update_asks_for_evidence(self):
        bot.emit_marketplace_dispute_notifications([9001], self.DISPUTE, "charge.dispute.updated")
        self.assertEqual(["dispute_action_required"], self.emit.events())

    def test_a_closed_dispute_sends_nothing_here(self):
        closed = dict(self.DISPUTE, status="won")
        bot.emit_marketplace_dispute_notifications([9001], closed, "charge.dispute.closed")
        self.assertEqual([], self.emit.calls)

    def test_an_update_with_no_deadline_does_not_ask_for_evidence(self):
        vague = dict(self.DISPUTE, evidence_details={})
        bot.emit_marketplace_dispute_notifications([9001], vague, "charge.dispute.updated")
        self.assertEqual([], self.emit.calls)


class PaidOrderWiring(WiringTestCase):
    def test_both_sides_are_emailed_and_neither_gets_a_second_in_app_row(self):
        bot.emit_marketplace_paid_order_emails([dict(TRANSACTION)])
        self.assertEqual({"payment_succeeded", "new_paid_order"}, set(self.emit.events()))
        for call in self.emit.calls:
            self.assertTrue(
                call["email_only"],
                "notify_user already wrote the in-app row; a second one shows the buyer one order twice",
            )
        buyer_call = self.emit.for_event("payment_succeeded")[0]
        seller_call = self.emit.for_event("new_paid_order")[0]
        self.assertEqual(BUYER, buyer_call["user_id"])
        self.assertEqual(SELLER, seller_call["user_id"])

    def test_the_seller_is_told_their_net_and_the_buyer_is_not(self):
        bot.emit_marketplace_paid_order_emails([dict(TRANSACTION)])
        seller_call = self.emit.for_event("new_paid_order")[0]
        buyer_call = self.emit.for_event("payment_succeeded")[0]
        self.assertEqual(4038, seller_call["context"]["seller_net_cents"])
        self.assertNotIn("seller_net_cents", buyer_call["context"])

    def test_the_item_title_comes_from_the_frozen_checkout_metadata(self):
        bot.emit_marketplace_paid_order_emails([dict(TRANSACTION)])
        self.assertEqual("Vintage 50mm lens", self.emit.calls[0]["context"]["item_summary"])

    def test_unparseable_metadata_still_sends_the_order(self):
        bot.emit_marketplace_paid_order_emails([dict(TRANSACTION, metadata_json="{not json")])
        self.assertEqual(2, len(self.emit.calls), "a bad title must not cost the seller their order email")

    def test_a_transaction_with_no_id_is_skipped(self):
        bot.emit_marketplace_paid_order_emails([{"buyer_user_id": BUYER}])
        self.assertEqual([], self.emit.calls)


class ApplicationWiring(unittest.TestCase):
    def test_every_reviewable_status_maps_to_an_event_that_exists(self):
        from services import payments_notifications

        for status, event in bot.SELLER_APPLICATION_EVENTS.items():
            self.assertIn(event, payments_notifications.SPECS, status)

    def test_an_unmapped_status_sends_nothing(self):
        emit = CapturingEmit()
        with mock.patch.object(bot, "emit_payment_notification", emit):
            bot.emit_seller_application_event(BUYER, "draft", {})
        self.assertEqual([], [call for call in emit.calls if call["event"]])


class TimestampFormatting(unittest.TestCase):
    def test_a_missing_timestamp_is_blank_not_a_placeholder(self):
        # The fact blocks drop empty rows, so "" hides the row entirely. Any
        # placeholder would be a date the seller could plan around.
        for value in (None, 0, "", "not a number", -1):
            self.assertEqual("", bot.stripe_timestamp_date(value))

    def test_a_real_timestamp_reads_as_a_date(self):
        self.assertEqual("September 21, 2026", bot.stripe_timestamp_date(1790000000))


if __name__ == "__main__":
    unittest.main()
