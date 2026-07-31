"""An approval that has been acted on must be marked as spent.

Found by reading the database back after Batch 22's live simulator run, not by any
test here — and the reason no test here found it is the interesting part.

`test_confirm_path.py::test_token_cannot_be_replayed` already asserts single use, and
passes. It asserts it against `crypto.alerts.delete`, whose confirmation policy is
`ALWAYS`. Every capability the single-use guard has ever been tested against is an
`ALWAYS` capability. `pause` and `resume` are `CONTEXTUAL`, and on the redemption pass
they take a different branch through the gateway — one that never redeems anything.

## The branch

`_agent_confirm` calls the gateway with `explicit_request=True`, which is truthful:
pressing Confirm is about as explicit as a person gets. `undx_agent_policy.evaluate`
then reaches the `CONTEXTUAL` arm, sees an explicit request against a single resolved
resource, and returns `ALLOW` with reason `explicit_single_resource`.

`ALLOW` means `decision.needs_confirmation` is `False`, and the gateway's entire
redemption block sits under `if decision.needs_confirmation:`. So the token that was
just presented is never looked at. The write runs, is verified, and is audited as
`confirmation_state="not_required"` with `confirmation_evidence="no_grant"`, while the
approval row stays `pending` with `consumed_at` null until it lapses on its own.

The comment above that block says "A required approval is either redeemed now or
requested now; there is no third branch in which execution proceeds anyway." This is
the third branch.

## What it costs

* The approval is replayable for the remainder of its TTL. `pending_confirmation_action`
  selects on `status='pending'`, and nothing ever moves it off `pending`.
* Batch 20 taught a dead approval to say which kind of dead it is. An approval that has
  been spent on this path never reaches `consumed`, so the one state whose message tells
  a person "it was already used, go and look" is unreachable — and that is the single
  state where "press it again" is the wrong thing to do.
* The audit row for a confirmed action does not name the approval that authorised it,
  so the trail cannot answer "what was this write authorised by".

The observed instance: `pulse_ai_confirmations` rows 6, 7 and 8 in the local database,
all `crypto.alerts.resume`, all `pending` with `consumed_at` null, against
`pulse_ai_tool_operations` row 46 recording the resume as `verified` at
`2026-07-31T02:45:06+00:00` with `confirmation_evidence: "no_grant"`.
"""

from __future__ import annotations

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from tests.undx_agent import bootstrap

bootstrap.install()

from tests.undx_agent.harness import AgentFixture, OWNER_ID  # noqa: E402


class SpentApprovalIsSpent(unittest.TestCase):

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

    def _hedged_pause(self, user_id: int = OWNER_ID) -> str:
        """Ask for a CONTEXTUAL write in a way that earns a card, and return the token.

        The phrasing matters and is asserted rather than assumed: the whole defect lives
        in the difference between a request that needs a card and a redemption that the
        policy engine thinks does not.
        """
        response = self.runtime.handle(
            self.fx.cur, user_id=user_id, text="can you pause my bitcoin alert",
        )
        self.fx.commit()
        self.assertEqual(
            response.status, "confirmation_required",
            "this test is meaningless unless the hedged phrasing actually earns a card",
        )
        token = response.card.get("confirmation_token")
        self.assertTrue(token, "a confirmation_required outcome must carry a token")
        return token

    def _approval_rows(self) -> list[dict]:
        self.fx.cur.execute(
            "SELECT action_id, status, consumed_at FROM pulse_ai_confirmations "
            "WHERE user_id=? ORDER BY id", (OWNER_ID,))
        return [{"action_id": r[0], "status": r[1], "consumed_at": r[2]}
                for r in self.fx.cur.fetchall()]

    def _verified_writes(self, tool_name: str = "pulsesoc.crypto_alerts.pause") -> list[dict]:
        self.fx.cur.execute(
            "SELECT status, confirmation_state, verification_json FROM pulse_ai_tool_operations "
            "WHERE user_id=? AND tool_name=? ORDER BY id", (OWNER_ID, tool_name))
        return [{"status": r[0], "confirmation_state": r[1],
                 "verification": json.loads(r[2] or "{}")} for r in self.fx.cur.fetchall()]

    # -- the defect -------------------------------------------------------

    def test_a_contextual_approval_is_marked_consumed_once_it_is_acted_on(self):
        token = self._hedged_pause()
        result = self.svc.confirm_action(OWNER_ID, {"confirmation_token": token})
        self.fx.commit()

        # The change really did happen — this is not a test about a broken write.
        self.assertTrue(result.get("ok"), result)
        self.assertEqual(self.fx.alert_status(self.alert_id), "paused")

        rows = self._approval_rows()
        self.assertEqual(len(rows), 1, rows)
        self.assertEqual(rows[0]["status"], "consumed", rows[0])
        self.assertIsNotNone(rows[0]["consumed_at"], rows[0])

    def test_a_contextual_token_cannot_be_replayed(self):
        """The same claim `test_confirm_path` makes for ALWAYS, made for CONTEXTUAL.

        Stated separately from the row check because they can fail independently: a
        redemption that burned the row but let execution through anyway would pass the
        test above and fail this one.
        """
        token = self._hedged_pause()
        first = self.svc.confirm_action(OWNER_ID, {"confirmation_token": token})
        self.fx.commit()
        self.assertTrue(first.get("ok"), first)

        second = self.svc.confirm_action(OWNER_ID, {"confirmation_token": token})
        self.fx.commit()
        self.assertFalse(second.get("ok"), second)
        self.assertNotEqual(second.get("status"), "verified_success", second)

    def test_the_replay_does_not_reach_the_executor_a_second_time(self):
        """Asserted at the audit table rather than at the response.

        A refusal that still ran the write would answer the test above correctly and be
        exactly the bug. Idempotency is not a defence here: the key is derived from the
        caller's request id, and a second press of Confirm carries a fresh one.
        """
        token = self._hedged_pause()
        self.svc.confirm_action(OWNER_ID, {"confirmation_token": token})
        self.fx.commit()
        after_first = self._verified_writes()

        self.svc.confirm_action(
            OWNER_ID, {"confirmation_token": token, "client_message_id": "second-press"})
        self.fx.commit()
        after_second = self._verified_writes()

        self.assertEqual(len(after_first), 1, after_first)
        self.assertEqual(
            len(after_second), len(after_first),
            f"the replay reached the executor again: {after_second}")

    def test_the_audit_row_names_the_grant_it_was_confirmed_against(self):
        """A confirmed write that audits as `not_required` cannot answer "authorised by what".

        Two fields, because they failed for two different reasons and can regress
        independently. `confirmation_evidence` was `no_grant` because no grant was ever
        redeemed. `confirmation_state` was `not_required` because it was read from the
        tool registry — a fact about the tool, recorded in a column about the operation
        — so it said the same thing for an approved write and an unasked one.
        """
        token = self._hedged_pause()
        self.svc.confirm_action(OWNER_ID, {"confirmation_token": token})
        self.fx.commit()

        writes = self._verified_writes()
        self.assertEqual(len(writes), 1, writes)
        self.assertEqual(writes[0]["confirmation_state"], "confirmed", writes[0])
        self.assertEqual(
            writes[0]["verification"].get("confirmation_evidence"), "grant_consumed", writes[0])

    def test_an_unconfirmed_write_is_not_labelled_confirmed(self):
        """The other side of the same column, so `confirmed` cannot become a constant."""
        self.runtime.handle(self.fx.cur, user_id=OWNER_ID, text=f"pause alert {self.alert_id}")
        self.fx.commit()

        writes = self._verified_writes()
        self.assertEqual(len(writes), 1, writes)
        self.assertEqual(writes[0]["confirmation_state"], "not_required", writes[0])

    # -- the gateway's own refusal ----------------------------------------

    def test_the_gateway_refuses_a_dead_token_presented_directly_to_it(self):
        """Written because a mutation survived, and the reason it survived is the point.

        `test_the_replay_does_not_reach_the_executor_a_second_time` passes with the
        gateway's `if not grant:` refusal deleted outright. Not because the refusal is
        wrong, but because that test replays through `confirm_action`, and
        `_agent_confirm` routes on `pending_confirmation_action` — which selects on
        `status='pending'`. Once the first press consumes the row the routing read finds
        nothing, `_agent_confirm` returns `None`, and the request falls through to the
        legacy branch and its 409. The gateway is never reached, so a test that goes
        through the service layer cannot say anything about what the gateway does.

        It is still the gateway that must refuse: it is the component that redeems, the
        runtime reaches it without passing through `confirm_action`, and a guard whose
        only proof is that some caller upstream happens to stop first is not a guard.
        So this presents a dead token to `execute` directly.
        """
        from services import undx_tool_gateway

        token = self._hedged_pause()
        self.svc.confirm_action(OWNER_ID, {"confirmation_token": token})
        self.fx.commit()
        before = self._verified_writes()

        outcome = undx_tool_gateway.execute(
            self.fx.cur, user_id=OWNER_ID, capability_id="crypto.alerts.pause",
            proposed_arguments={"alert_id": self.alert_id},
            confirmation_token=token, explicit_request=True,
            request_id="direct-replay", client_request_id="direct-replay",
        )
        self.fx.commit()

        receipt = outcome.receipt
        self.assertEqual(receipt.status, "confirmation_required", receipt)
        self.assertEqual(
            (receipt.evidence or {}).get("reason"), "grant_not_redeemable", receipt)
        self.assertEqual(
            self._verified_writes(), before,
            "a spent token presented straight to the gateway still reached the executor")

    # -- what the audit layer is told -------------------------------------

    def test_the_gateway_tells_the_audit_layer_a_grant_was_redeemed(self):
        """Asserted at the call, because the column cannot hold this on its own.

        `record_tool_result` recomputes `confirmation_state` from the redeemed grant when
        the operation finishes, so the value `begin_tool_operation` wrote is overwritten
        on every operation that completes. That makes `confirmed=` invisible to a test
        that reads the finished row — a mutation setting it to a constant survives the
        whole file — while remaining load-bearing in the one case that matters: an
        operation that begins and never finishes. A write that crashes mid-flight leaves
        the row exactly as `begin` wrote it, and that is the row a person investigating
        an interrupted change has to read.

        So the argument is asserted where it is passed.
        """
        from unittest import mock
        from services import undx_architecture

        seen: list[bool] = []
        real = undx_architecture.begin_tool_operation

        def spy(*args, **kwargs):
            seen.append(bool(kwargs.get("confirmed")))
            return real(*args, **kwargs)

        token = self._hedged_pause()
        with mock.patch.object(undx_architecture, "begin_tool_operation", spy):
            self.svc.confirm_action(OWNER_ID, {"confirmation_token": token})
        self.fx.commit()

        self.assertEqual(seen, [True], f"the write was not begun as an approved one: {seen}")

    def test_an_unapproved_write_is_not_begun_as_an_approved_one(self):
        """The other side, so `confirmed=True` cannot be hardcoded to pass the test above."""
        from unittest import mock
        from services import undx_architecture

        seen: list[bool] = []
        real = undx_architecture.begin_tool_operation

        def spy(*args, **kwargs):
            seen.append(bool(kwargs.get("confirmed")))
            return real(*args, **kwargs)

        with mock.patch.object(undx_architecture, "begin_tool_operation", spy):
            self.runtime.handle(
                self.fx.cur, user_id=OWNER_ID, text=f"pause alert {self.alert_id}")
        self.fx.commit()

        self.assertEqual(seen, [False], f"an unasked write was begun as approved: {seen}")

    def test_the_reserved_row_names_the_grant_before_the_write_finishes(self):
        """The crash window, asserted directly on the reservation.

        This is the state the previous two tests protect the input to: what a person
        reading `pulse_ai_tool_operations` sees for an operation that was authorised and
        then never got to record its outcome.
        """
        from services import undx_architecture

        prepared = undx_architecture.prepare_tool_operation(
            OWNER_ID, "pulsesoc.crypto_alerts.pause", "crash-window", "alert_rule:1")
        undx_architecture.begin_tool_operation(
            self.fx.cur, OWNER_ID, prepared, "corr", confirmed=True)
        self.fx.commit()

        self.fx.cur.execute(
            "SELECT confirmation_state, status FROM pulse_ai_tool_operations "
            "WHERE operation_id=?", (prepared["operation_id"],))
        row = self.fx.cur.fetchone()
        self.assertEqual(row[1], "pending", row)
        self.assertEqual(row[0], "confirmed", row)

    # -- what must not regress --------------------------------------------

    def test_an_always_capability_still_burns_its_approval(self):
        """The case that was already covered, kept here so a fix cannot trade one for the other."""
        response = self.runtime.handle(
            self.fx.cur, user_id=OWNER_ID, text="delete my bitcoin alert")
        self.fx.commit()
        self.assertEqual(response.status, "confirmation_required")
        token = response.card["confirmation_token"]

        self.assertTrue(self.svc.confirm_action(OWNER_ID, {"confirmation_token": token}).get("ok"))
        self.fx.commit()
        self.assertEqual(self.fx.alert_status(self.alert_id), "deleted")
        self.assertEqual([r["status"] for r in self._approval_rows()], ["consumed"])

    def test_an_unhedged_contextual_request_still_needs_no_card(self):
        """The policy arm this batch must not break.

        "pause alert N" against one resolved resource is its own approval and has always
        executed without a card. The fix is about redeeming a token that *was* presented,
        not about demanding one that was never needed.
        """
        response = self.runtime.handle(
            self.fx.cur, user_id=OWNER_ID, text=f"pause alert {self.alert_id}")
        self.fx.commit()
        self.assertNotEqual(response.status, "confirmation_required", response.status)
        self.assertEqual(self.fx.alert_status(self.alert_id), "paused")
        self.assertEqual(self._approval_rows(), [])


if __name__ == "__main__":
    unittest.main()
