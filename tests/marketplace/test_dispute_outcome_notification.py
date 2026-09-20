"""Who is told how a chargeback ended.

`charge.dispute.created` and `.updated` already reach the seller. The *outcome*
did not: `charge.dispute.closed` fell through every notification branch, so a
seller whose earnings had just been reversed by a lost chargeback was told by
nobody — not by PulseSoc, and not by the separate in-platform dispute system in
``services/business_os/marketplace``, which tracks buyer-opened disputes on its
own tables and never sees a Stripe event.

The gap was not a missing template. It was that the only notification branch
reading a dispute at all read `seller_transaction_id` off `data.object`
metadata, and a Dispute's metadata is its own and empty — the reason
``pulse_marketplace_reversal_transaction_ids`` exists.
"""

from __future__ import annotations

import ast
import inspect
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import bot  # noqa: E402
from services import payments_notifications  # noqa: E402


BUYER = 4101
SELLER = 4102
OTHER_SELLER = 4103


TRANSACTION = {
    "id": 9001,
    "buyer_user_id": BUYER,
    "seller_user_id": SELLER,
    "amount_cents": 4250,
    "currency": "usd",
}

#: A Dispute as Stripe actually delivers it: no `metadata`, because the
#: checkout metadata belongs to the Charge, not to this object.
DISPUTE = {
    "id": "dp_1",
    "amount": 4250,
    "currency": "usd",
    "reason": "product_not_received",
    "status": "lost",
}


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


class OutcomeTestCase(unittest.TestCase):
    PARTIES = {9001: dict(TRANSACTION)}

    def setUp(self):
        self.emit = CapturingEmit()
        patches = [
            mock.patch.object(bot, "emit_payment_notification", self.emit),
            mock.patch.object(bot, "marketplace_transaction_parties",
                              lambda tx_ids: {k: v for k, v in self.PARTIES.items() if k in set(tx_ids)}),
            mock.patch.object(bot, "seller_first_names",
                              lambda user_ids: {SELLER: "Ada", OTHER_SELLER: "Grace"}),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def close(self, outcomes, tx_ids, dispute=None, event_type="charge.dispute.closed"):
        bot.emit_marketplace_dispute_outcome_notifications(
            outcomes, tx_ids, dispute if dispute is not None else DISPUTE, event_type)


class TheSellerLearnsTheOutcome(OutcomeTestCase):
    def test_a_metadata_free_dispute_still_reaches_the_seller(self):
        # The whole defect. This object carries no `seller_transaction_id`, and
        # is exactly what Stripe delivers; the ids come from the payment-intent
        # fallback the caller resolves.
        self.assertNotIn("metadata", DISPUTE)
        self.close([], [9001])
        self.assertEqual(["dispute_lost"], self.emit.events())
        self.assertEqual(SELLER, self.emit.calls[0]["user_id"])

    def test_a_lost_dispute_names_the_money_and_the_dispute(self):
        self.close([], [9001])
        context = self.emit.calls[0]["context"]
        self.assertEqual("dp_1", context["dispute_id"])
        self.assertEqual("product not received", context["dispute_reason"])
        self.assertEqual("Ada", context["seller_first_name"])
        self.assertEqual("#9001", context["order_reference"])

    def test_a_won_dispute_tells_the_seller_the_hold_is_lifted(self):
        self.close([], [9001], dict(DISPUTE, status="won"))
        self.assertEqual(["dispute_won"], self.emit.events())
        self.assertEqual(SELLER, self.emit.calls[0]["user_id"])

    def test_a_closed_inquiry_is_also_an_all_clear(self):
        # `warning_closed` never became a real dispute, but the seller was told
        # on creation that the payout was on hold. Silence would leave that
        # standing as the last thing they heard.
        self.close([], [9001], dict(DISPUTE, status="warning_closed"))
        self.assertEqual(["dispute_won"], self.emit.events())

    def test_the_buyer_is_not_emailed_the_sellers_outcome(self):
        self.close([], [9001])
        self.assertNotIn(BUYER, [call["user_id"] for call in self.emit.calls])


class AStrandedWinIsNotAWin(OutcomeTestCase):
    STRANDED = {"seller_transaction_id": 9001, "dispute_id": "dp_1", "action": "needs_review"}

    def test_a_stranded_won_dispute_tells_the_seller_nothing(self):
        # `pulse_apply_marketplace_dispute` refuses the transition and opens a
        # critical PAYOUT_STATE_CONFLICT incident for a human. "You won, the
        # hold is lifted" is the one reading that stops anybody going to look —
        # the same reason the order row takes `dispute_won_review` rather than
        # `dispute_resolved`.
        self.close([self.STRANDED], [9001], dict(DISPUTE, status="won"))
        self.assertEqual([], self.emit.calls)

    def test_a_stranded_order_does_not_silence_its_releasable_neighbour(self):
        parties = {9001: dict(TRANSACTION),
                   9002: dict(TRANSACTION, id=9002, seller_user_id=OTHER_SELLER)}
        with mock.patch.object(self, "PARTIES", parties), \
             mock.patch.object(bot, "marketplace_transaction_parties",
                               lambda tx_ids: {k: v for k, v in parties.items() if k in set(tx_ids)}):
            self.close([self.STRANDED], [9001, 9002], dict(DISPUTE, status="won"))
        self.assertEqual(["dispute_won"], self.emit.events())
        self.assertEqual(OTHER_SELLER, self.emit.calls[0]["user_id"])


class OneDisputeOneNotificationPerRecipient(OutcomeTestCase):
    def test_a_cart_dispute_sends_one_notification_carrying_both_orders(self):
        # One chargeback can span several of a seller's orders. The engine
        # dedupes on `dispute_id`, so emitting per transaction would have the
        # second order's money silently dropped instead of counted.
        parties = {9001: dict(TRANSACTION),
                   9002: dict(TRANSACTION, id=9002, amount_cents=1000)}
        with mock.patch.object(bot, "marketplace_transaction_parties",
                               lambda tx_ids: {k: v for k, v in parties.items() if k in set(tx_ids)}):
            self.close([], [9001, 9002])
        self.assertEqual(["dispute_lost"], self.emit.events())
        self.assertEqual(5250, self.emit.calls[0]["context"]["amount_cents"])

    def test_two_sellers_on_one_charge_are_told_separately(self):
        parties = {9001: dict(TRANSACTION),
                   9002: dict(TRANSACTION, id=9002, seller_user_id=OTHER_SELLER)}
        with mock.patch.object(bot, "marketplace_transaction_parties",
                               lambda tx_ids: {k: v for k, v in parties.items() if k in set(tx_ids)}):
            self.close([], [9001, 9002])
        self.assertEqual({SELLER, OTHER_SELLER}, {call["user_id"] for call in self.emit.calls})

    def test_a_redelivery_carries_the_key_that_suppresses_it(self):
        first = payments_notifications.build_event("dispute_lost", SELLER, {"dispute_id": "dp_1"})
        replay = payments_notifications.build_event("dispute_lost", SELLER, {"dispute_id": "dp_1"})
        self.assertEqual(first["dedupe_key"], replay["dedupe_key"])

    def test_the_outcome_is_not_deduped_against_the_opening(self):
        opened = payments_notifications.build_event("dispute_opened", SELLER, {"dispute_id": "dp_1"})
        lost = payments_notifications.build_event("dispute_lost", SELLER, {"dispute_id": "dp_1"})
        self.assertNotEqual(opened["dedupe_key"], lost["dedupe_key"],
                            "an outcome suppressed by the opening is the silence this fixes")


class TheFigureIsWhatThisSellerLost(OutcomeTestCase):
    def test_a_lost_dispute_reports_the_allocator_not_the_whole_charge(self):
        # The dispute's own `amount` is the whole charge. Telling a seller who
        # lost $10 of a $42.50 cart charge that $42.50 was taken back is a false
        # statement about money, in writing.
        parties = {9002: dict(TRANSACTION, id=9002)}
        with mock.patch.object(bot, "marketplace_transaction_parties",
                               lambda tx_ids: {k: v for k, v in parties.items() if k in set(tx_ids)}):
            self.close(
                [{"settlement": {"seller_transaction_id": 9002}, "total_refund_minor": 1000}],
                [9002],
            )
        self.assertEqual(1000, self.emit.calls[0]["context"]["amount_cents"])

    def test_a_replayed_allocation_is_not_counted_twice(self):
        with mock.patch.object(bot, "marketplace_transaction_parties",
                               lambda tx_ids: {9001: dict(TRANSACTION)}):
            self.close(
                [{"settlement": {"seller_transaction_id": 9001}, "total_refund_minor": 1000,
                  "duplicate": True}],
                [9001],
            )
        self.assertEqual(4250, self.emit.calls[0]["context"]["amount_cents"],
                         "a duplicate allocation must fall back to the order, not add to it")


class NothingElseIsNotified(OutcomeTestCase):
    def test_an_open_dispute_is_not_this_lanes_business(self):
        for event_type in ("charge.dispute.created", "charge.dispute.updated"):
            with self.subTest(event_type=event_type):
                self.emit.calls.clear()
                self.close([], [9001], dict(DISPUTE, status="needs_response"), event_type)
                self.assertEqual([], self.emit.calls)

    def test_a_close_with_no_terminal_verdict_says_nothing(self):
        self.close([], [9001], dict(DISPUTE, status="under_review"))
        self.assertEqual([], self.emit.calls)

    def test_no_resolvable_transactions_says_nothing(self):
        self.close([], [])
        self.assertEqual([], self.emit.calls)


class TheWebhookHasOneWriterPerDispute(unittest.TestCase):
    """The raw-metadata block must no longer claim any dispute.

    Its dispute arms never fired — a Dispute carries no `seller_transaction_id`.
    But they were a second writer waiting to wake up: had Stripe ever populated
    dispute metadata, they would have overwritten
    ``pulse_apply_marketplace_dispute``'s `dispute_lost` / `dispute_won_review`
    with `dispute_resolved` and sent a second notification for the one dispute.
    """

    @staticmethod
    def _webhook_ast():
        # `ast` never splits on U+2028/U+2029, so the node is taken whole rather
        # than sliced back out of the source by line index.
        source = inspect.getsource(bot.stripe_webhook)
        return ast.parse(source.lstrip())

    def test_the_metadata_block_emits_only_for_refunds(self):
        disputed = []
        for node in ast.walk(self._webhook_ast()):
            if not isinstance(node, ast.Call):
                continue
            name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            if name != "pulse_emit_payment_checkout_event":
                continue
            rendered = ast.unparse(node)
            if "dispute" in rendered:
                disputed.append(rendered)
        self.assertEqual(
            [], disputed,
            "the checkout-event lane must not write a dispute; pulse_apply_marketplace_dispute owns that row",
        )

    def test_the_outcome_lane_is_wired_after_the_handler(self):
        rendered = ast.unparse(self._webhook_ast())
        self.assertIn("emit_marketplace_dispute_outcome_notifications", rendered,
                      "the outcome lane must be reachable from the webhook or nobody is told")
        self.assertLess(
            rendered.index("pulse_apply_marketplace_dispute("),
            rendered.index("emit_marketplace_dispute_outcome_notifications("),
            "an outcome email must not go out ahead of the settlement move it describes",
        )


class TheStatusVocabularyIsShared(unittest.TestCase):
    def test_the_row_statuses_the_handler_writes_are_the_ones_meant_here(self):
        # If these drift, the order row and the email disagree about the same
        # dispute — and the row is what the seller sees next to the message.
        rendered = ast.unparse(ast.parse(
            inspect.getsource(bot.pulse_apply_marketplace_dispute).lstrip()))
        for status in ("dispute_lost", "dispute_resolved", "dispute_won_review"):
            self.assertIn(f"'{status}'", rendered)

    def test_a_stranded_row_has_no_notification_event(self):
        self.assertNotIn("dispute_won_review", payments_notifications.SPECS,
                         "a stranded win is an incident for a human, not a message to the seller")


if __name__ == "__main__":
    unittest.main()
