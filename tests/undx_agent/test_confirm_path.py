"""The confirmation endpoint, now shared by two executors.

``confirm_action`` served exactly one action for its whole life and was hardcoded to
it. It now has to route, which introduces a class of bug the original could not have:
sending a token to the wrong executor. The tests here are mostly about what must *not*
happen on that path — a wrong guess must never burn a valid approval, and the legacy
notification contract must survive unchanged.
"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from tests.undx_agent import bootstrap

bootstrap.install()

from tests.undx_agent.harness import AgentFixture, OTHER_ID, OWNER_ID  # noqa: E402


class ConfirmRouting(unittest.TestCase):

    def setUp(self) -> None:
        self.fx = AgentFixture().start()
        from services import pulse_ai_service, undx_agent_runtime

        self.svc = pulse_ai_service
        self.runtime = undx_agent_runtime
        bootstrap.stub_bot(pulse_ai_service)
        self.svc.ensure_schema(self.fx.cur, self.fx.conn)
        self.fx.commit()
        self.alert_id = self.fx.make_alert(OWNER_ID, symbol="BTC")

    def tearDown(self) -> None:
        self.fx.stop()

    # -- helpers ----------------------------------------------------------

    def _request_delete(self, user_id: int = OWNER_ID) -> str:
        """Ask for a consequential write and return the minted approval token."""
        response = self.runtime.handle(
            self.fx.cur, user_id=user_id, text="delete my bitcoin alert",
        )
        self.fx.commit()
        self.assertEqual(response.status, "confirmation_required")
        token = response.card.get("confirmation_token")
        self.assertTrue(token, "a confirmation_required outcome must carry a token")
        return token

    # -- the happy path ---------------------------------------------------

    def test_agent_token_executes_and_verifies(self):
        token = self._request_delete()
        result = self.svc.confirm_action(OWNER_ID, {"confirmation_token": token})
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["status"], "verified_success")
        self.assertEqual(result["action_id"], "crypto.alerts.delete")
        self.assertEqual(result["verification_state"], "verified")
        # ``delete`` is a soft delete: the row survives with a terminal status, which
        # is what makes the read-back verification meaningful.
        self.assertEqual(self.fx.alert_status(self.alert_id), "deleted")

    def test_confirmation_card_shows_the_change_before_it_happens(self):
        """Consent needs a subject. The card must name the current and proposed state."""
        response = self.runtime.handle(
            self.fx.cur, user_id=OWNER_ID, text="delete my bitcoin alert")
        self.fx.commit()
        self.assertEqual(response.card["current_value"], "active")
        self.assertEqual(response.card["proposed_value"], "deleted")
        self.assertEqual(self.fx.alert_status(self.alert_id), "active")

    # -- single use -------------------------------------------------------

    def test_token_cannot_be_replayed(self):
        token = self._request_delete()
        first = self.svc.confirm_action(OWNER_ID, {"confirmation_token": token})
        self.assertTrue(first["ok"])
        second = self.svc.confirm_action(OWNER_ID, {"confirmation_token": token})
        self.assertFalse(second.get("ok"))
        self.assertNotEqual(second.get("status"), "verified_success")

    def test_token_is_bound_to_its_owner(self):
        """A leaked token is not a capability. Another account cannot spend it."""
        token = self._request_delete()
        stolen = self.svc.confirm_action(OTHER_ID, {"confirmation_token": token})
        self.assertFalse(stolen.get("ok"))
        self.assertEqual(self.fx.alert_status(self.alert_id), "active")
        # And it is still good for the person it was issued to.
        self.assertTrue(self.svc.confirm_action(OWNER_ID, {"confirmation_token": token})["ok"])

    # -- routing must not destroy approvals -------------------------------

    def test_routing_read_does_not_consume(self):
        """The peek that chooses an executor must leave the approval spendable.

        This is the specific regression the non-consuming lookup exists to prevent:
        an earlier design consumed first and dispatched afterwards, so every token
        that took the wrong branch was destroyed on the way.
        """
        token = self._request_delete()
        peeked = self.svc.undx_architecture.pending_confirmation_action(
            self.fx.cur, OWNER_ID, token)
        self.assertEqual(peeked["action_id"], "crypto.alerts.delete")
        peeked_again = self.svc.undx_architecture.pending_confirmation_action(
            self.fx.cur, OWNER_ID, token)
        self.assertEqual(peeked_again["action_id"], "crypto.alerts.delete")
        self.assertTrue(self.svc.confirm_action(OWNER_ID, {"confirmation_token": token})["ok"])

    def test_peek_is_owner_scoped_and_silent(self):
        token = self._request_delete()
        self.assertEqual(
            self.svc.undx_architecture.pending_confirmation_action(self.fx.cur, OTHER_ID, token), {})
        self.assertEqual(
            self.svc.undx_architecture.pending_confirmation_action(self.fx.cur, OWNER_ID, "not-a-token"), {})

    def test_client_cannot_restate_the_approved_arguments(self):
        """Redemption replays the server's record, not the request body.

        The client sends a different ``alert_id`` alongside a valid token. If the
        endpoint honoured it, one approval would authorise a write to any resource.
        """
        other_alert = self.fx.make_alert(OWNER_ID, symbol="ETH")
        token = self._request_delete()
        result = self.svc.confirm_action(
            OWNER_ID, {"confirmation_token": token, "alert_id": other_alert,
                       "arguments": {"alert_id": other_alert}})
        self.assertTrue(result["ok"])
        # ``delete`` is a soft delete: the row survives with a terminal status, which
        # is what makes the read-back verification meaningful.
        self.assertEqual(self.fx.alert_status(self.alert_id), "deleted")
        self.assertEqual(self.fx.alert_status(other_alert), "active")

    # -- the legacy branch is untouched -----------------------------------

    def test_unknown_token_hits_the_legacy_flag_gate_first(self):
        """With V4/V5 off — the fixture default — the legacy 503 is still what comes back.

        The agent branch declines an unrecognised token and returns ``None``; control
        then reaches the original flag check in its original position. The point of the
        assertion is the error code: adding a second executor must not change what an
        account with the legacy actions disabled sees.
        """
        result = self.svc.confirm_action(OWNER_ID, {"confirmation_token": "junk"})
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "undx_actions_disabled")
        self.assertEqual(result["http_status"], 503)

    def test_unknown_token_with_legacy_enabled_is_still_a_409(self):
        self.fx.set_flags(UNDX_V4_ACTIONS="1", UNDX_V4_DISABLE_WRITES="")
        result = self.svc.confirm_action(OWNER_ID, {"confirmation_token": "junk"})
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "confirmation_invalid")
        self.assertEqual(result["http_status"], 409)

    def test_missing_token_still_yields_the_legacy_refusal(self):
        self.fx.set_flags(UNDX_V4_ACTIONS="1", UNDX_V4_DISABLE_WRITES="")
        result = self.svc.confirm_action(OWNER_ID, {})
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "confirmation_required")
        self.assertEqual(result["http_status"], 400)

    def test_agent_token_is_not_executable_by_the_legacy_branch(self):
        """Even with both systems live, an agent token cannot reach the V4/V5 executor.

        The legacy branch passes ``expect_action_id='notifications.preference.update'``
        into ``consume_confirmation``, so the binding refuses a ``crypto.alerts.delete``
        grant before burning it. Here the agent is off, which forces exactly that path.
        """
        token = self._request_delete()
        self.fx.set_flags(UNDX_AGENT_ENABLED="", UNDX_V4_ACTIONS="1", UNDX_V4_DISABLE_WRITES="")
        result = self.svc.confirm_action(OWNER_ID, {"confirmation_token": token})
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "confirmation_invalid")
        self.assertEqual(self.fx.alert_status(self.alert_id), "active")
        # The mis-routed attempt did not spend the approval, so it still works when the
        # agent comes back.
        self.fx.set_flags(UNDX_AGENT_ENABLED="1")
        self.assertTrue(self.svc.confirm_action(OWNER_ID, {"confirmation_token": token})["ok"])

    def test_agent_disabled_falls_through_to_legacy(self):
        """With the agent off, its own tokens are simply unknown to the legacy branch.

        They are not executed and they are not specially reported. That is the correct
        behaviour for a kill switch: the capability disappears rather than degrading.
        """
        token = self._request_delete()
        self.fx.set_flags(UNDX_AGENT_ENABLED="")
        result = self.svc.confirm_action(OWNER_ID, {"confirmation_token": token})
        self.assertFalse(result["ok"])
        self.assertEqual(self.fx.alert_status(self.alert_id), "active")

    def test_writes_disabled_refuses_a_valid_token(self):
        """The approval survives; the execution does not.

        A kill switch thrown between approval and redemption must stop the write. It
        must not, however, pretend the approval was invalid.
        """
        token = self._request_delete()
        self.fx.set_flags(UNDX_AGENT_WRITES_ENABLED="")
        result = self.svc.confirm_action(OWNER_ID, {"confirmation_token": token})
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "permission_denied")
        self.assertEqual(result["http_status"], 403)
        self.assertEqual(self.fx.alert_status(self.alert_id), "active")

    # -- what happens when the redemption itself goes wrong ----------------

    def test_a_failure_after_execution_is_not_reported_as_an_invalid_approval(self):
        """The worst outcome this endpoint can produce, and the one it used to produce.

        ``_agent_confirm`` was wrapped in a catch-all that returned ``None``, which the
        caller reads as "not an agent token" and hands to the legacy branch — where the
        token, already burned, is rejected as invalid. So a delete that had *completed*
        answered 409 "this confirmation is no longer valid". The user is then told
        nothing happened, and the obvious next move is to try again.

        The failure is injected after the gateway has been entered, which is precisely
        the region the old handler covered and the new one deliberately does not.
        """
        from services import undx_tool_gateway

        token = self._request_delete()
        original = undx_tool_gateway.execute
        undx_tool_gateway.execute = (
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom after entry"))
        )
        try:
            result = self.svc.confirm_action(OWNER_ID, {"confirmation_token": token})
        finally:
            undx_tool_gateway.execute = original

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "confirmation_outcome_unknown")
        self.assertEqual(result["http_status"], 202)
        self.assertEqual(result["verification_state"], "verification_pending")
        # Not 409, and not the legacy refusal. Those would both tell the user the
        # approval was bad, which is the one thing that is definitely untrue.
        self.assertNotEqual(result["http_status"], 409)
        # And the advice in the message must not be "try again", because a second
        # attempt against a spent token cannot succeed either.
        self.assertIn("Check the screen", result["message"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
