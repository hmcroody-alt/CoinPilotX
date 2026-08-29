"""A switched-off capability must not be reported as an unclear sentence.

``scripts/undx_production_gate_probe.py`` found this: with
``UNDX_AGENT_ENABLED_CAPABILITIES`` set to a list that omits ``feed.posts.like``,
"Like my most recent post." came back as *"Which post? Tell me its number, or open
it and ask again."*

The mechanism is worth stating, because the bug is not where it looks. The policy
denies a withdrawn capability correctly at ``undx_agent_policy.evaluate`` step 3 —
but that check lives in the gateway, and the gateway is entered only after arguments
resolve. Resolution answers first. An allowlist narrow enough to omit
``feed.posts.like`` also omits ``feed.posts.list``, so the supporting read that turns
"my most recent post" into a row is refused by ``_read_permitted``, ``post_id`` stays
empty, and an empty required field is a clarification.

Every step of that is locally correct and the result is a lie about which thing went
wrong. The person is told their words were ambiguous when the words were understood
and the product is off; they answer with a post id, and are refused the same way. A
rollout state has to read as a rollout state, so the withdrawal is now checked before
resolution runs.
"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from tests.undx_agent import bootstrap

bootstrap.install()

from tests.undx_agent.harness import AgentFixture, OWNER_ID  # noqa: E402


class WithdrawnCapabilityIsReportedAsUnavailable(unittest.TestCase):

    def setUp(self) -> None:
        self.fx = AgentFixture().start()
        self.addCleanup(self.fx.stop)
        from services import undx_agent_runtime

        self.runtime = undx_agent_runtime
        self.post_id = self.fx.make_post(body="Launch day is getting closer",
                                         created_at="2026-08-20T00:00:00")

    def _turn(self, text: str):
        return self.runtime.handle(self.fx.cur, user_id=OWNER_ID, text=text,
                                   conversation_id=1, confirmation_token="",
                                   client_request_id="", correlation_id="test")

    def _withdraw_like(self) -> None:
        """An allowlist that keeps the agent on but omits the capability under test.

        Written as an allowlist rather than a denylist because that is the shape the
        probe found in production-plausible configuration, and because it is the one
        that also disables the supporting read — which is what produced the wrong
        message in the first place.
        """
        self.fx.set_flags(UNDX_AGENT_ENABLED_CAPABILITIES="crypto.alerts.pause")
        self.addCleanup(self.fx.set_flags, UNDX_AGENT_ENABLED_CAPABILITIES="")

    def test_a_withdrawn_capability_is_not_reported_as_an_ambiguous_target(self):
        self._withdraw_like()
        response = self._turn("Like my most recent post")
        self.assertTrue(response.handled,
                        "a recognised sentence must still be answered, not dropped")
        self.assertEqual(response.card.get("status"), "permission_denied")
        self.assertNotIn("Which post", response.reply)
        self.assertNotIn("Tell me its number", response.reply)

    def test_the_reply_says_the_action_is_unavailable(self):
        self._withdraw_like()
        response = self._turn("Like my most recent post")
        # The wording belongs to the policy, so this asserts the sentence the policy
        # actually publishes rather than a copy of it that could drift.
        from services import undx_agent_policy

        spec = self.runtime.require("feed.posts.like")
        expected = undx_agent_policy.evaluate(OWNER_ID, spec, {}).message
        self.assertEqual(response.reply, expected)
        self.assertEqual(response.capability_id, "feed.posts.like")

    def test_naming_the_post_by_number_is_refused_the_same_way(self):
        """The loop this closes.

        Being asked "which post?" invites the person to supply an id. If that path
        answered differently, the fix would only have moved the confusion one turn
        later — so the explicit id has to reach the same refusal.
        """
        self._withdraw_like()
        response = self._turn(f"Like post {self.post_id}")
        self.assertEqual(response.card.get("status"), "permission_denied")
        self.assertNotIn("Which post", response.reply)

    def test_nothing_was_written(self):
        self._withdraw_like()
        self._turn("Like my most recent post")
        self._turn("Yes")
        from services.feed_intelligence_service import get_post_like

        self.assertFalse(bool(get_post_like(OWNER_ID, self.post_id)))

    # -- the capability still enabled: unchanged behaviour ------------------

    def test_an_enabled_capability_still_asks_for_confirmation(self):
        """The guard must not become a second authorization point.

        A capability that is on has to reach the gateway exactly as before, card and
        all. Asserting the healthy path here is the only thing that distinguishes
        "reports withdrawal correctly" from "refuses everything".
        """
        response = self._turn("Like my most recent post")
        self.assertEqual(response.card.get("status"), "confirmation_required")

    def test_an_enabled_capability_still_asks_which_post_when_it_genuinely_cannot_tell(self):
        """Target ambiguity is a real category and still has to be reachable.

        With no post named and no recency phrase to resolve, "Which post?" is the
        correct answer, and a fix that suppressed it would have traded one wrong
        category for another.
        """
        self.fx.set_flags(UNDX_AGENT_ENABLED_CAPABILITIES="feed.posts.like")
        self.addCleanup(self.fx.set_flags, UNDX_AGENT_ENABLED_CAPABILITIES="")
        response = self._turn("Like that post")
        self.assertIn("Which post", response.reply)


if __name__ == "__main__":
    unittest.main()
