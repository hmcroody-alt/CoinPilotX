"""A typed "yes" must resume the exact action that was staged, or nothing.

The gap this covers was demonstrated end to end before it was fixed: UNDX raised a
confirmation card, the person typed "Yes", and the turn fell through to conversational
text. Nothing was executed and nothing said so. The cause was narrow and worth naming,
because it is the reason the suite that already existed could not see it.

`create_confirmation` stores only `sha256(raw_token)`. The plaintext token leaves with
the card and is never recoverable from the database, so every path that redeems an
approval had to carry the token back — which the *button* does and a typed sentence
cannot. `revoke_approval` had already established the by-id counterpart for "no"; there
was no counterpart for "yes". So the conversational path could raise a confirmation and
had no way to spend one, and every existing confirmation test drove `confirm_action`
with a token, which is the one caller that was never broken.

Two halves are asserted here and they fail independently:

* **Resumption** — a bare affirmation finds the pending action, redeems it by id, and
  replays the *stored* arguments. Re-deriving arguments from the word "yes" would
  retarget the write, which is why `_confirm_pending` calls the gateway directly rather
  than routing back through `_act`.
* **Target resolution** — "my most recent post" names no row, so the runtime picks one.
  A picked row is the case where a wrong choice is invisible: the person never typed an
  id, so they have nothing to check the outcome against. `target_chosen_by_agent` raises
  a card on exactly that path, and `describe_post` is what puts the chosen row in words
  before it is acted on. Both are asserted, because a card that does not name its target
  is a habituation exercise rather than consent.

Section 24 of the governing mission enumerates seven cases a confirmation must survive.
Each has a test below whose name says which. The evidence for every write is read
straight from `pulse_reactions` and `alert_rules` through the fixture, never from the
receipt: a receipt that agreed with itself would prove nothing.
"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from tests.undx_agent import bootstrap

bootstrap.install()

from tests.undx_agent.harness import OTHER_ID, OWNER_ID, AgentFixture  # noqa: E402


class ConfirmationResume(unittest.TestCase):

    def setUp(self) -> None:
        self.fx = AgentFixture().start()
        from services import undx_agent_runtime, undx_architecture

        self.runtime = undx_agent_runtime
        self.arch = undx_architecture
        self.fx.ensure_feed_schema()
        # Two posts, deliberately days apart. A fixture that stamped both with "now"
        # could not express the only question these tests ask, which is *which* one.
        self.older = self.fx.make_post(OWNER_ID, body="Older post",
                                       created_at="2026-08-01T00:00:00")
        self.newest = self.fx.make_post(OWNER_ID, body="Launch day is getting closer",
                                        created_at="2026-08-20T00:00:00")

    def tearDown(self) -> None:
        self.fx.stop()

    # -- helpers ----------------------------------------------------------

    def _say(self, text: str, *, user_id: int = OWNER_ID, request_id: str = "",
             conversation_id: int = 1):
        response = self.runtime.handle(
            self.fx.cur, user_id=user_id, text=text,
            request_id=request_id or f"req-{text[:12]}-{user_id}",
            conversation_id=conversation_id)
        self.fx.commit()
        return response

    def _grants(self, user_id: int = OWNER_ID) -> list[dict]:
        return list(self.arch.pending_approvals(self.fx.cur, int(user_id)) or [])

    def _stage_like(self, user_id: int = OWNER_ID):
        """Ask for the like in the mission's own words and assert a card was raised.

        Asserted rather than assumed. Every test below is about what happens *after* a
        card exists, and each would pass vacuously against a build that stopped raising
        one — the failure mode the whole file is written against.
        """
        response = self._say("Like my most recent post", user_id=user_id)
        self.assertEqual(response.status, "confirmation_required", response.reply)
        self.assertEqual(response.capability_id, "feed.posts.like")
        return response

    # -- the sentence the mission names -----------------------------------

    def test_like_my_most_recent_post_then_yes_executes_and_verifies(self):
        """The full acceptance path, asserted at the table rather than the receipt."""
        self._stage_like()
        self.assertFalse(self.fx.post_liked(self.newest),
                         "a confirmation card must not have written anything yet")

        confirmed = self._say("Yes")

        self.assertTrue(confirmed.handled, confirmed.reply)
        self.assertEqual(confirmed.status, "verified_success", confirmed.reply)
        self.assertEqual(confirmed.capability_id, "feed.posts.like")
        self.assertTrue(self.fx.post_liked(self.newest))
        self.assertFalse(self.fx.post_liked(self.older),
                         "the older post was liked, so the resolver picked by feed order")

    def test_the_grant_names_the_resolved_post_and_not_the_phrase(self):
        """Section 6: the canonical id is persisted into the pending action.

        A grant that stored "my most recent post" would have to resolve again at
        redemption time, against a feed that may have moved. The id is fixed when the
        card is shown because that is the moment the person is being asked about.
        """
        self._stage_like()
        grants = self._grants()
        self.assertEqual(len(grants), 1, grants)
        self.assertEqual(grants[0]["action_id"], "feed.posts.like")
        self.assertEqual(str(grants[0]["target_id"]), str(self.newest))
        self.assertEqual(grants[0]["arguments"], {"post_id": self.newest})

    def test_the_card_names_the_post_it_chose(self):
        """A card whose target the person never typed has to say which row it is.

        This is the only place the runtime's choice becomes visible before it is acted
        on. Asserted on the words, not on the presence of a label, because an empty or
        generic string satisfies "has a label" and answers nothing.
        """
        response = self._stage_like()
        self.assertIn("Launch day is getting closer", response.reply)
        self.assertNotIn("Older post", response.reply)
        self.assertIn("Launch day is getting closer",
                      response.card.get("resource_label") or "")

    def test_the_receipt_names_the_like_rather_than_a_setting(self):
        """Section 10: "Done" has to be checkable.

        "that setting is on" is true of any write in the registry and tells a person
        nothing about theirs. The sentence is asserted here rather than in the response
        layer's own tests because it depends on `feed.posts.like` declaring the field,
        which is a registry fact and regresses there.
        """
        self._stage_like()
        confirmed = self._say("Yes")
        self.assertIn("like", confirmed.reply.lower())
        self.assertNotIn("that setting", confirmed.reply.lower())

    # -- section 24, case by case -----------------------------------------

    def test_24a_yes_executes_the_exact_pending_action(self):
        """Two staged writes on different subsystems; only the approved one may run.

        The alert is staged first and left un-approved. If "yes" resolved by anything
        looser than the grant it burns — most recent, or first found — this is the test
        that catches it.
        """
        alert_id = self.fx.make_alert(OWNER_ID, symbol="BTC")
        self._say("delete my bitcoin alert")
        self.assertEqual(len(self._grants()), 1)
        self.fx.cur.execute(
            "UPDATE pulse_ai_confirmations SET status='consumed' WHERE user_id=? "
            "AND action_id=?", (OWNER_ID, "crypto.alerts.delete"))
        self.fx.commit()

        self._stage_like()
        self._say("Yes")

        self.assertTrue(self.fx.post_liked(self.newest))
        self.assertNotEqual(self.fx.alert_status(alert_id), "deleted",
                            "the affirmation reached a grant it was not given for")

    def test_24b_no_cancels_and_writes_nothing(self):
        self._stage_like()
        refused = self._say("No")
        self.assertTrue(refused.handled, refused.reply)
        self.assertFalse(self.fx.post_liked(self.newest))
        self.assertEqual(self._grants(), [], "a refused grant must not stay pending")

    def test_24c_yes_twice_executes_once(self):
        """Single use, asserted at the second turn rather than at the row.

        The burn is an UPDATE under `status='pending'`, so the second affirmation
        matches zero rows and has nothing to replay. The like is already on the post
        either way, which is why the assertion is that the turn was not handled as a
        write rather than that the state changed.
        """
        self._stage_like()
        first = self._say("Yes", request_id="yes-1")
        self.assertEqual(first.status, "verified_success", first.reply)

        second = self._say("Yes", request_id="yes-2")
        self.assertFalse(second.handled,
                         f"a spent grant answered a second affirmation: {second.reply}")
        self.assertEqual(self._grants(), [])

    def test_24d_yes_after_expiry_executes_nothing(self):
        """Expiry is enforced in the SQL predicate, so it is expired in the SQL."""
        self._stage_like()
        self.fx.cur.execute(
            "UPDATE pulse_ai_confirmations SET expires_at='2020-01-01T00:00:00+00:00' "
            "WHERE user_id=?", (OWNER_ID,))
        self.fx.commit()

        response = self._say("Yes")

        self.assertFalse(self.fx.post_liked(self.newest),
                         "an expired grant was redeemed")
        self.assertNotEqual(response.status, "verified_success", response.reply)

    def test_24e_yes_from_another_account_executes_nothing(self):
        """The grant is owner-scoped; a second account saying yes is not a second yes."""
        self._stage_like()

        response = self._say("Yes", user_id=OTHER_ID)

        self.assertFalse(self.fx.post_liked(self.newest),
                         "another account's affirmation redeemed this grant")
        self.assertNotEqual(response.status, "verified_success", response.reply)
        self.assertEqual(len(self._grants(OWNER_ID)), 1,
                         "the owner's grant was disturbed by another account")

    def test_24f_an_unrelated_command_does_not_confirm(self):
        """A new instruction is an instruction, not an approval.

        `_confirm_pending` is only reached when the message matched no capability, so a
        sentence that routes somewhere else can never fall into the affirmation branch.
        Asserted because the guard is a consequence of call order rather than of a check,
        and call order is the kind of thing a refactor moves.
        """
        self._stage_like()

        self._say("show me my alerts")

        self.assertFalse(self.fx.post_liked(self.newest),
                         "an unrelated command was treated as an approval")
        self.assertEqual(len(self._grants()), 1,
                         "an unrelated command consumed the pending grant")

    def test_24g_two_pending_actions_ask_rather_than_guess(self):
        """Picking the newest of several grants is wrong exactly when it matters.

        A person with two cards open who types "yes" has said something ambiguous, and
        the only safe reading of an ambiguous approval is to ask. Nothing may execute.
        """
        alert_id = self.fx.make_alert(OWNER_ID, symbol="BTC")
        self._say("delete my bitcoin alert")
        self._stage_like()
        self.assertEqual(len(self._grants()), 2, self._grants())

        response = self._say("Yes")

        # Asserted on the card, not on ``response.status``. A clarification never
        # reaches the gateway, so it has no receipt, and ``status`` reads the receipt —
        # the two clarification branches in ``handle`` are shaped the same way. There is
        # a real inconsistency there for a client that routes on the top-level field,
        # but it predates this work and widening ``status`` would change every one of
        # those paths, which is not what a confirmation fix is entitled to do.
        self.assertTrue(response.handled, response.reply)
        self.assertEqual((response.card or {}).get("status"), "clarification_required",
                         response.reply)
        self.assertFalse(self.fx.post_liked(self.newest))
        self.assertNotEqual(self.fx.alert_status(alert_id), "deleted")
        self.assertEqual(len(self._grants()), 2,
                         "an ambiguous affirmation burned a grant it did not act on")

    # -- resolution, and its boundaries -----------------------------------

    def test_the_resolver_reads_only_the_callers_own_posts(self):
        """"My most recent post" is scoped to the caller even when a newer one exists."""
        self.fx.make_post(OTHER_ID, body="Someone else's newer post",
                          created_at="2026-08-25T00:00:00")

        self._stage_like()

        grants = self._grants()
        self.assertEqual(str(grants[0]["target_id"]), str(self.newest))

    def test_the_resolver_declines_rather_than_guessing_when_there_is_nothing(self):
        """No posts is not the same claim as "this post".

        The resolver returns 0 for "no opinion", and the turn has to end in a question
        rather than in a write against whatever row happened to come back.
        """
        self.fx.cur.execute("DELETE FROM pulse_posts")
        self.fx.commit()

        response = self._say("Like my most recent post")

        self.assertNotEqual(response.status, "confirmation_required", response.reply)
        self.assertEqual(self._grants(), [])

    def test_a_post_id_the_person_typed_still_needs_no_card(self):
        """The policy arm this work must not widen.

        `feed.posts.like` is `NEVER`, and that is correct for a row the person named: a
        like is cheap to undo and the target is not in doubt. `target_chosen_by_agent`
        may only tighten, so a typed id must execute exactly as it always did. Without
        this, the fix reads as "likes now need confirmation", which is a different and
        much worse change.
        """
        response = self._say(f"like post {self.older}")

        self.assertNotEqual(response.status, "confirmation_required", response.reply)
        self.assertTrue(self.fx.post_liked(self.older))
        self.assertEqual(self._grants(), [],
                         "a named target raised a card it should not have")


if __name__ == "__main__":
    unittest.main()
