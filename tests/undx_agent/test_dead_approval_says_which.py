"""A confirmation button that no longer works has to say which kind of dead it is.

``consume_confirmation`` returns ``None`` for six unrelated situations, and until now
``confirm_action`` answered all six with one sentence:

    That confirmation expired, was already used, or belongs to another account.

Five of those situations mean nothing happened. One of them — ``consumed`` — means the
write was already attempted, and quite possibly succeeded. A person who taps Confirm,
sees no change, and reads that sentence concludes "nothing happened, do it again". For
the one case where that conclusion is wrong, acting on it repeats a write.

The obvious fix is the wrong one. Naming the state for any token would let anybody with
a guessed string learn whether it names a real approval, and whether somebody else holds
one. The collapse in ``pending_confirmation_action`` is a deliberate security property,
not an oversight:

    an unknown, expired, spent or foreign token yields an empty result, all four
    indistinguishable from each other.

So ``approval_state`` narrows rather than loosens: it is filtered on ``user_id``, and a
row belonging to another account takes exactly the same branch as a row that does not
exist. The owner of an approval learns what happened to their own approval; nobody
learns anything about anybody else's. Both halves are asserted here, and the second half
is the one that would be quietly lost by a later refactor.

The fifth state is the one inspection misses: an approval that is still **live** and
simply cannot be executed by the branch that received it. It has not expired, it has not
been spent, and reporting it as "already used" would be the same lie in the other
direction.
"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from tests.undx_agent import bootstrap

bootstrap.install()

from tests.undx_agent.harness import AgentFixture, OTHER_ID, OWNER_ID  # noqa: E402


LEGACY_ON = {"UNDX_V4_ACTIONS": "1", "UNDX_V4_DISABLE_WRITES": ""}

#: The sentence this batch exists to delete. Asserted by absence rather than described,
#: so that reintroducing it fails a test instead of passing review.
THE_OLD_SENTENCE = ("That confirmation expired, was already used, or belongs to another "
                    "account.")


class DeadApprovalBase(unittest.TestCase):

    def setUp(self) -> None:
        self.fx = AgentFixture().start()
        from services import pulse_ai_service, undx_agent_runtime, undx_architecture

        self.svc = pulse_ai_service
        self.runtime = undx_agent_runtime
        self.arch = undx_architecture
        bootstrap.stub_bot(pulse_ai_service)
        self.svc.ensure_schema(self.fx.cur, self.fx.conn)
        self.fx.commit()
        self.alert_id = self.fx.make_alert(OWNER_ID, symbol="BTC")

    def tearDown(self) -> None:
        self.fx.stop()

    # -- helpers ----------------------------------------------------------

    def _token(self, user_id: int = OWNER_ID) -> str:
        response = self.runtime.handle(
            self.fx.cur, user_id=user_id, text="delete my bitcoin alert")
        self.fx.commit()
        self.assertEqual(response.status, "confirmation_required")
        token = response.card.get("confirmation_token")
        self.assertTrue(token)
        return token

    def _force_status(self, status: str) -> None:
        """Put the single approval row into a terminal status directly.

        Written rather than provoked because three of these statuses are reached only
        through paths this test is not about, and a test that has to drive a notification
        preference into a race to observe ``stale_state`` is testing the race.
        """
        self.fx.cur.execute(
            "UPDATE pulse_ai_confirmations SET status=? WHERE status='pending'", (status,))
        self.fx.commit()

    def _expire(self) -> None:
        self.fx.cur.execute(
            "UPDATE pulse_ai_confirmations SET expires_at='2000-01-01T00:00:00+00:00' "
            "WHERE status='pending'")
        self.fx.commit()


class TheStateItselfTests(DeadApprovalBase):
    """``approval_state`` — owner-scoped, non-consuming, and total."""

    def test_a_fresh_approval_is_live(self):
        token = self._token()
        self.assertEqual(self.arch.APPROVAL_LIVE,
                         self.arch.approval_state(self.fx.cur, OWNER_ID, token))

    def test_a_lapsed_approval_is_expired_even_though_no_row_says_so(self):
        """Nothing writes an ``expired`` status. The deadline is the only evidence."""
        token = self._token()
        self._expire()
        self.fx.cur.execute("SELECT status FROM pulse_ai_confirmations LIMIT 1")
        self.assertEqual("pending", self.fx.cur.fetchone()["status"])
        self.assertEqual(self.arch.APPROVAL_EXPIRED,
                         self.arch.approval_state(self.fx.cur, OWNER_ID, token))

    def test_a_spent_approval_is_consumed(self):
        token = self._token()
        self.assertTrue(self.svc.confirm_action(OWNER_ID, {"confirmation_token": token})["ok"])
        self.fx.commit()
        self.assertEqual(self.arch.APPROVAL_CONSUMED,
                         self.arch.approval_state(self.fx.cur, OWNER_ID, token))

    def test_a_withdrawn_approval_is_revoked(self):
        token = self._token()
        self.arch.revoke_confirmation(self.fx.cur, OWNER_ID, token)
        self.fx.commit()
        self.assertEqual(self.arch.APPROVAL_REVOKED,
                         self.arch.approval_state(self.fx.cur, OWNER_ID, token))

    def test_a_superseded_approval_is_named_as_such(self):
        token = self._token()
        self._force_status("stale_state")
        self.assertEqual(self.arch.APPROVAL_SUPERSEDED,
                         self.arch.approval_state(self.fx.cur, OWNER_ID, token))

    def test_a_spent_and_lapsed_approval_reports_spent(self):
        """Both facts are true; only one determines whether the change happened.

        It was redeemed while it was live. Reporting "expired" would tell the person
        nothing happened, which is the exact error this batch exists to remove.
        """
        token = self._token()
        self.assertTrue(self.svc.confirm_action(OWNER_ID, {"confirmation_token": token})["ok"])
        self.fx.cur.execute(
            "UPDATE pulse_ai_confirmations SET expires_at='2000-01-01T00:00:00+00:00'")
        self.fx.commit()
        self.assertEqual(self.arch.APPROVAL_CONSUMED,
                         self.arch.approval_state(self.fx.cur, OWNER_ID, token))

    def test_a_status_nobody_taught_it_is_unknown_rather_than_echoed(self):
        """A column value invented by a later migration must not become a sentence."""
        token = self._token()
        self._force_status("some_future_status")
        self.assertEqual(self.arch.APPROVAL_UNKNOWN,
                         self.arch.approval_state(self.fx.cur, OWNER_ID, token))

    def test_every_state_has_a_sentence(self):
        """The map is total, so a new state cannot ship without wording.

        ``confirm_action`` indexes ``APPROVAL_STATE_MESSAGE`` directly rather than using
        ``.get`` with a default: a missing key should be a loud failure in a test, not a
        quiet fallback in front of a person.
        """
        states = {value for name, value in vars(self.arch).items()
                  if name.startswith("APPROVAL_") and isinstance(value, str)}
        self.assertEqual(6, len(states))
        for state in states:
            self.assertIn(state, self.arch.APPROVAL_STATE_MESSAGE)
            self.assertTrue(self.arch.APPROVAL_STATE_MESSAGE[state].strip())

    def test_reading_the_state_does_not_change_it(self):
        """Asking the question must not be able to answer it differently next time."""
        token = self._token()
        for _ in range(3):
            self.assertEqual(self.arch.APPROVAL_LIVE,
                             self.arch.approval_state(self.fx.cur, OWNER_ID, token))
        self.assertTrue(self.svc.confirm_action(OWNER_ID, {"confirmation_token": token})["ok"])


class NobodyLearnsAboutSomebodyElsesApprovalTests(DeadApprovalBase):
    """The property the old collapsed sentence was protecting, kept intact."""

    def test_a_foreign_token_and_a_fabricated_one_are_the_same_answer(self):
        token = self._token(OWNER_ID)
        self.assertEqual(self.arch.APPROVAL_UNKNOWN,
                         self.arch.approval_state(self.fx.cur, OTHER_ID, token))
        self.assertEqual(self.arch.APPROVAL_UNKNOWN,
                         self.arch.approval_state(self.fx.cur, OTHER_ID, "not-a-token"))

    def test_the_owners_own_view_of_that_same_row_is_unaffected(self):
        """Scoping narrows who is told, not what is true."""
        token = self._token(OWNER_ID)
        self.assertEqual(self.arch.APPROVAL_LIVE,
                         self.arch.approval_state(self.fx.cur, OWNER_ID, token))

    def test_a_spent_row_is_still_invisible_to_another_account(self):
        """The interesting case: 'consumed' is the state worth probing for."""
        token = self._token(OWNER_ID)
        self.assertTrue(self.svc.confirm_action(OWNER_ID, {"confirmation_token": token})["ok"])
        self.fx.commit()
        self.assertEqual(self.arch.APPROVAL_UNKNOWN,
                         self.arch.approval_state(self.fx.cur, OTHER_ID, token))

    def test_the_endpoint_tells_a_stranger_nothing_either(self):
        """End to end, not just at the primitive.

        A leaked token presented by the wrong account must produce the same answer as a
        string somebody made up.
        """
        token = self._token(OWNER_ID)
        self.fx.set_flags(UNDX_AGENT_ENABLED="", **LEGACY_ON)
        stolen = self.svc.confirm_action(OTHER_ID, {"confirmation_token": token})
        invented = self.svc.confirm_action(OTHER_ID, {"confirmation_token": "junk"})
        self.assertEqual(invented["message"], stolen["message"])
        self.assertEqual(invented.get("reason"), stolen.get("reason"))
        self.assertEqual("unknown", stolen.get("reason"))


class TheSentenceThePersonReadsTests(DeadApprovalBase):
    """What comes back from ``confirm_action`` when the button no longer works."""

    def _dead(self, token: str) -> dict:
        result = self.svc.confirm_action(OWNER_ID, {"confirmation_token": token})
        self.assertFalse(result["ok"])
        return result

    def test_the_old_one_size_sentence_is_gone(self):
        """No state may still be answered with it, including the unknown one.

        ``unknown`` is the state the old sentence was least wrong about, which makes it
        the one most likely to keep the wording out of inertia. It covers a fabricated
        token and a foreign one — never a spent one — so "was already used" does not
        belong in it either.
        """
        self.fx.set_flags(**LEGACY_ON)
        self.assertNotEqual(THE_OLD_SENTENCE, self._dead("junk")["message"])
        for sentence in self.arch.APPROVAL_STATE_MESSAGE.values():
            self.assertNotEqual(THE_OLD_SENTENCE, sentence)

    def test_a_spent_approval_does_not_read_as_nothing_happened(self):
        """The one case where "try again" is the wrong instruction.

        Asserted negatively as well as positively: it is not enough that the new
        sentence mentions reuse, it must not also carry the phrase that sends the person
        back to repeat the write.
        """
        self.fx.set_flags(**LEGACY_ON)
        token = self._token()
        self.assertTrue(self.svc.confirm_action(OWNER_ID, {"confirmation_token": token})["ok"])
        result = self._dead(token)
        self.assertEqual("consumed", result["reason"])
        self.assertIn("already used", result["message"])
        self.assertNotIn("nothing changed", result["message"].lower())

    def test_an_expired_approval_says_nothing_changed(self):
        self.fx.set_flags(**LEGACY_ON)
        token = self._token()
        self._expire()
        result = self._dead(token)
        self.assertEqual("expired", result["reason"])
        self.assertIn("nothing changed", result["message"].lower())

    def test_a_revoked_approval_says_it_was_cancelled(self):
        self.fx.set_flags(**LEGACY_ON)
        token = self._token()
        self.arch.revoke_confirmation(self.fx.cur, OWNER_ID, token)
        self.fx.commit()
        result = self._dead(token)
        self.assertEqual("revoked", result["reason"])
        self.assertIn("cancelled", result["message"])

    def test_a_live_approval_is_not_reported_as_used_or_expired(self):
        """The fifth case, and the one inspection misses.

        With the agent switched off, its own token reaches the legacy branch, which
        refuses it on the action binding without burning it. The approval is untouched
        and still good — so the sentence must say so, and the row must survive to prove
        the sentence was true.
        """
        token = self._token()
        self.fx.set_flags(UNDX_AGENT_ENABLED="", **LEGACY_ON)
        result = self._dead(token)
        self.assertEqual("live", result["reason"])
        self.assertIn("has not been used", result["message"])
        self.assertEqual("active", self.fx.alert_status(self.alert_id))
        # And the claim is checkable: turn the agent back on and it still spends.
        self.fx.set_flags(UNDX_AGENT_ENABLED="1")
        self.assertTrue(self.svc.confirm_action(OWNER_ID, {"confirmation_token": token})["ok"])

    def test_every_dead_answer_says_whether_anything_changed(self):
        """The property the person actually needs, asserted across all six.

        Each sentence must resolve "did my tap do anything?" — either by saying nothing
        changed, or by saying the write was already attempted. A sentence that resolves
        neither is the defect wearing new words.
        """
        for state, sentence in self.arch.APPROVAL_STATE_MESSAGE.items():
            lowered = sentence.lower()
            self.assertTrue(
                "nothing changed" in lowered or "already been attempted" in lowered,
                f"{state}: {sentence!r} leaves the person guessing")


class ReachableInTheConfigurationThatShipsTests(DeadApprovalBase):
    """The legacy executor is off everywhere the agent runs. This must still answer.

    ``UNDX_V4_ACTIONS`` is absent from ``.env.local`` and from the running backend,
    because the agent replaced that executor rather than joining it. Every test above
    that turns it on is describing a configuration nobody deploys. Without the ordering
    asserted here, a dead agent-minted approval would reach the flag gate and be
    answered "UNDX actions are currently read-only for this account" — a sentence that
    is false for an account the agent is enabled for, and false in the direction that
    hides a write which already happened.
    """

    def test_a_spent_approval_is_answered_even_with_the_legacy_executor_off(self):
        token = self._token()
        self.assertTrue(self.svc.confirm_action(OWNER_ID, {"confirmation_token": token})["ok"])
        self.fx.commit()
        result = self.svc.confirm_action(OWNER_ID, {"confirmation_token": token})
        self.assertEqual("consumed", result["reason"])
        self.assertNotEqual("undx_actions_disabled", result["error"])

    def test_an_expired_approval_is_answered_with_the_legacy_executor_off(self):
        token = self._token()
        self._expire()
        self.assertEqual("expired",
                         self.svc.confirm_action(OWNER_ID, {"confirmation_token": token})["reason"])

    def test_a_stranger_still_only_gets_the_kill_switch(self):
        """The disclosure test, at the branch that runs before the executor gate.

        A spent approval is the one worth probing for. Another account presenting it
        must get the same answer as somebody presenting nonsense — here, the 503.
        """
        token = self._token(OWNER_ID)
        self.assertTrue(self.svc.confirm_action(OWNER_ID, {"confirmation_token": token})["ok"])
        self.fx.commit()
        stolen = self.svc.confirm_action(OTHER_ID, {"confirmation_token": token})
        invented = self.svc.confirm_action(OTHER_ID, {"confirmation_token": "junk"})
        self.assertEqual("undx_actions_disabled", stolen["error"])
        self.assertEqual(invented["message"], stolen["message"])

    def test_a_live_approval_still_reports_the_kill_switch(self):
        """A switch that is off is what stopped it, and that is what it should say.

        The dead-state answer is for changes that already finished. Borrowing it for a
        grant that is still good would replace one misleading sentence with another.
        """
        token = self._token()
        self.fx.set_flags(UNDX_AGENT_ENABLED="")
        result = self.svc.confirm_action(OWNER_ID, {"confirmation_token": token})
        self.assertEqual("undx_actions_disabled", result["error"])
        self.assertEqual(503, result["http_status"])
        self.assertEqual("active", self.fx.alert_status(self.alert_id))


class TheContractDidNotMoveTests(DeadApprovalBase):
    """Existing clients key off ``error`` and the status code. Neither changes."""

    def test_the_error_code_and_status_are_unchanged(self):
        self.fx.set_flags(**LEGACY_ON)
        result = self.svc.confirm_action(OWNER_ID, {"confirmation_token": "junk"})
        self.assertEqual("confirmation_invalid", result["error"])
        self.assertEqual(409, result["http_status"])
        self.assertFalse(result["ok"])

    def test_the_flag_gate_still_runs_before_any_of_this(self):
        """With the legacy actions off, the 503 arrives first and no state is read."""
        result = self.svc.confirm_action(OWNER_ID, {"confirmation_token": "junk"})
        self.assertEqual("undx_actions_disabled", result["error"])
        self.assertNotIn("reason", result)

    def test_a_missing_token_is_still_the_400_and_not_a_state(self):
        self.fx.set_flags(**LEGACY_ON)
        result = self.svc.confirm_action(OWNER_ID, {})
        self.assertEqual("confirmation_required", result["error"])
        self.assertEqual(400, result["http_status"])

    def test_the_success_path_never_consults_the_state(self):
        """``approval_state`` runs only on the failure branch.

        Asserted by making it explode. A successful redemption that touched it would
        fail here, which is what keeps a diagnostic read out of the hot path.
        """
        token = self._token()
        original = self.arch.approval_state

        def _boom(*_args, **_kwargs):
            raise AssertionError("approval_state must not run on the success path")

        self.arch.approval_state = _boom  # type: ignore[assignment]
        try:
            self.assertTrue(
                self.svc.confirm_action(OWNER_ID, {"confirmation_token": token})["ok"])
        finally:
            self.arch.approval_state = original  # type: ignore[assignment]


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
