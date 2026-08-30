"""A run executes an approval a person granted, or it does not execute.

The durable run path moves execution outside the request that asked for it, which
removes the one property everything else in this system leans on: that a person is
present. These tests are about what replaces it.

The claim under test is narrow. A run is not permission to act on somebody's account;
it is a record that they already gave permission, in a request, over arguments a
deterministic resolver produced from their own words. So the failures worth writing
down are the ones where a run behaves like the first thing rather than the second: a
run without an approval, a run whose approval has lapsed, a run claimed twice, a run
re-claimed after a crash and executed a second time.

Each is asserted against the real gateway and a real SQLite database rather than a
mock, for the reason the harness docstring gives: a mocked gateway has no
``WHERE user_id=?``, and every property here is a property of that clause.
"""

from __future__ import annotations

import json
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from tests.undx_agent import bootstrap

bootstrap.install()

from tests.undx_agent.harness import AgentFixture, OTHER_ID, OWNER_ID  # noqa: E402


RUN_FLAGS = {
    "UNDX_AGENT_RUNS_ENABLED": "1",
    "UNDX_WORKER_ENABLED": "1",
    "UNDX_BRAIN_ENABLED": "1",
}


class RunsCarryAnApprovalOrTheyDoNotExist(unittest.TestCase):
    """Enqueue is the boundary. Everything downstream trusts what it wrote."""

    def setUp(self) -> None:
        self.fx = AgentFixture(**RUN_FLAGS).start()
        from services import undx_agent_runs

        self.runs = undx_agent_runs
        self.runs.ensure_schema(self.fx.cur)
        self.fx.commit()

    def tearDown(self) -> None:
        self.fx.stop()

    def _enqueue(self, **overrides):
        """Queue a run, minting a real approval for it unless the test says otherwise.

        The default is a genuine confirmation row bound to the same capability, target
        and arguments the run carries, because that is the only state in which a write
        is queueable and therefore the only honest default. A test that wants a
        fabricated or absent id passes ``confirmation_id`` explicitly.
        """
        payload = {
            "user_id": OWNER_ID,
            "capability_id": "crypto.alerts.pause",
            "arguments": {"alert_id": 1},
            "client_request_id": "req_1",
        }
        payload.update(overrides)
        if "confirmation_id" not in payload:
            payload["confirmation_id"] = self.fx.grant_confirmation(
                payload["capability_id"], payload["arguments"],
                user_id=payload["user_id"],
            )
        return self.runs.enqueue(self.fx.cur, **payload)

    def test_a_write_cannot_be_created_without_a_confirmation(self) -> None:
        """The invariant, asserted as a refusal rather than a default.

        A write with a missing confirmation id is not a run with an empty field. It is a
        request to change an account because software decided to, and there must be no
        shape of this row that represents it.
        """
        from services.undx_agent_contracts import AgentError

        with self.assertRaises(AgentError) as caught:
            self._enqueue(confirmation_id="")
        self.assertEqual(getattr(caught.exception, "code", ""), "unconfirmed_run")

        self.fx.cur.execute("SELECT COUNT(*) FROM undx_agent_runs")
        self.assertEqual(int(self.fx.cur.fetchone()[0]), 0,
                         "a refused enqueue must leave no row behind")

    def test_a_read_may_be_queued_without_a_confirmation(self) -> None:
        """Because the gateway will never ask that read for one.

        This is the precise statement of the rule, and it is narrower than "every run was
        confirmed" rather than weaker than it. 72 of the 120 declared capabilities are
        read-only with ``confirmation=never``; no honest request can mint an approval for
        one, because minting requires a confirmation card the policy engine will never
        raise. Demanding an id there would not make a read safer — it would make reads
        unqueueable, and a rule satisfiable only by fabricating an approval is worse than
        no rule at all.

        What must remain true is that the row carries whatever the gateway is going to
        demand of it. The next test asserts the other half: that a read cannot become a
        write in the gap between queueing and executing.
        """
        run_id = self._enqueue(capability_id="reels.performance.summary",
                               arguments={"reel_id": 1}, confirmation_id="",
                               client_request_id="req_read")
        self.fx.commit()
        self.fx.cur.execute("SELECT confirmation_id, status FROM undx_agent_runs WHERE run_id=?",
                            (run_id,))
        row = dict(self.fx.cur.fetchone())
        self.assertEqual(row["confirmation_id"], "")
        self.assertEqual(row["status"], "queued")

    def test_a_run_naming_an_unregistered_capability_is_refused(self) -> None:
        """The allowlist is applied at the queue as well as at the gateway.

        A row naming a capability that does not exist can only ever be executed by a
        registry that gains it later — which is exactly the drift a queue makes possible
        and a request does not, because a request is decided and executed under one
        deployment.
        """
        from services.undx_agent_contracts import AgentError

        with self.assertRaises(AgentError) as caught:
            self._enqueue(capability_id="pulsesoc.not_a_real_capability",
                          confirmation_id="undx_confirm_irrelevant")
        self.assertEqual(getattr(caught.exception, "code", ""), "unsupported_capability")

    def test_an_approval_for_a_different_target_does_not_authorise_this_run(self) -> None:
        """Stage 4, asserted at the queue rather than deferred to the gateway.

        This is the shape the binding exists for: a real, pending, unexpired approval
        belonging to the right person, for the right capability — and for somebody
        else's alert. Without the binding the row would queue cleanly and the mismatch
        would surface at execution, after a claim and an attempt, to a person who has
        closed the app. Here it is refused into the request that caused it.
        """
        from services.undx_agent_contracts import AgentError

        elsewhere = self.fx.grant_confirmation("crypto.alerts.pause", {"alert_id": 99})
        with self.assertRaises(AgentError) as caught:
            self._enqueue(arguments={"alert_id": 1}, confirmation_id=elsewhere)
        self.assertEqual(getattr(caught.exception, "code", ""), "confirmation_not_bound")

        self.fx.cur.execute("SELECT COUNT(*) FROM undx_agent_runs")
        self.assertEqual(int(self.fx.cur.fetchone()[0]), 0)

    def test_an_approval_for_a_different_capability_does_not_authorise_this_run(self) -> None:
        """The same target under a different verb is a different act.

        Pausing an alert and deleting it name the same row and are not the same
        permission, so the capability id is bound as well as the target.
        """
        from services.undx_agent_contracts import AgentError

        other_verb = self.fx.grant_confirmation("crypto.alerts.delete", {"alert_id": 1})
        with self.assertRaises(AgentError) as caught:
            self._enqueue(capability_id="crypto.alerts.pause", arguments={"alert_id": 1},
                          confirmation_id=other_verb)
        self.assertEqual(getattr(caught.exception, "code", ""), "confirmation_not_bound")

    def test_another_persons_approval_does_not_authorise_this_run(self) -> None:
        """The binding read is owner-scoped in the statement, so a leaked id is not a key."""
        from services.undx_agent_contracts import AgentError

        theirs = self.fx.grant_confirmation("crypto.alerts.pause", {"alert_id": 1},
                                            user_id=OTHER_ID)
        with self.assertRaises(AgentError) as caught:
            self._enqueue(user_id=OWNER_ID, arguments={"alert_id": 1}, confirmation_id=theirs)
        self.assertEqual(getattr(caught.exception, "code", ""), "confirmation_not_bound")

    def test_a_queued_read_that_became_a_write_is_refused_at_execution(self) -> None:
        """The registry is re-read at execution, not trusted from the row.

        A run enqueued while a capability was read-only carries no approval. If a later
        release reclassified that capability as a write, the row is unchanged — it stores
        a capability id, not a risk class — and executing it would perform an unapproved
        write using a permission granted for a read. So the question is asked again,
        against the registry this process actually loaded.
        """
        from unittest import mock

        run_id = self._enqueue(capability_id="reels.performance.summary",
                               arguments={"reel_id": 1}, confirmation_id="",
                               client_request_id="req_drift")
        self.fx.commit()

        # The reclassified capability is expressed as a stand-in rather than by mutating
        # the real registry entry, because ``CapabilitySpec`` refuses at construction to
        # be a write without a verifier and a target field. That refusal is correct and
        # is tested elsewhere; here it would only stop us building the state we are
        # trying to survive. What the run path reads is the risk class, and this supplies
        # exactly that.
        class PromotedToWrite:
            capability_id = "reels.performance.summary"
            is_write = True
            confirmation = "always"

        claimed = self.runs.claim_next(self.fx.cur, "worker_drift")
        self.assertEqual(claimed["run_id"], run_id)
        with mock.patch.object(self.runs.undx_capability_registry, "get",
                               return_value=PromotedToWrite()):
            outcome = self.runs.execute_claimed(self.fx.cur, claimed, "worker_drift")

        self.assertFalse(outcome["executed"])
        self.assertEqual(outcome["reason"], "unconfirmed_run")
        self.fx.cur.execute("SELECT status FROM undx_agent_runs WHERE run_id=?", (run_id,))
        self.assertEqual(str(self.fx.cur.fetchone()[0]), "failed")

    def test_a_run_cannot_be_created_without_an_authenticated_owner(self) -> None:
        from services.undx_agent_contracts import AgentError

        for bad in (0, -1):
            with self.assertRaises(AgentError):
                self._enqueue(user_id=bad)

    def test_one_run_per_owner_and_request(self) -> None:
        """Two runs for one request would race to redeem one single-use approval.

        Neither could write twice — the gateway's idempotency key is derived from the
        same ``client_request_id`` — but the loser would report "that confirmation is
        no longer valid" about an action that in fact succeeded. Uniqueness at the
        queue keeps that contradiction out of the queue.

        The second call is *answered*, not refused. This used to assert an exception,
        which was the unique index speaking rather than this module, and it made a
        retried tap look to the caller like queueing had broken — the one reading that
        sends a caller back to executing the action a second time. The invariant is the
        row count and the identity of the run, so both are asserted directly rather than
        inferred from a raise.
        """
        first = self._enqueue(client_request_id="req_same")
        self.fx.commit()
        second = self._enqueue(client_request_id="req_same")
        self.fx.commit()
        self.assertEqual(second, first)
        self.fx.cur.execute(
            "SELECT COUNT(*) FROM undx_agent_runs WHERE user_id=? AND client_request_id=?",
            (OWNER_ID, "req_same"),
        )
        self.assertEqual(int(self.fx.cur.fetchone()[0]), 1)

    def test_a_request_id_reused_for_different_work_is_refused(self) -> None:
        """A collision is not a retry.

        The test above returns the earlier run because the second call *is* the earlier
        request. The same id naming a different capability is a different request wearing
        the same name, and handing back the first run would report progress on an action
        nobody asked for the second time.
        """
        from services.undx_agent_contracts import AgentError

        self._enqueue(client_request_id="req_same")
        self.fx.commit()
        with self.assertRaises(AgentError) as caught:
            self._enqueue(client_request_id="req_same",
                          capability_id="crypto.alerts.delete")
        self.assertEqual(caught.exception.code, "request_id_reused")

    def test_the_same_request_id_may_belong_to_two_different_people(self) -> None:
        """The uniqueness is per owner. Client request ids are not globally unique and
        must not have to be — two phones can generate the same one."""
        self.fx.cur.execute("DELETE FROM undx_agent_runs")
        self._enqueue(user_id=OWNER_ID, client_request_id="req_shared")
        self._enqueue(user_id=OTHER_ID, client_request_id="req_shared")
        self.fx.commit()
        self.fx.cur.execute("SELECT COUNT(*) FROM undx_agent_runs")
        self.assertEqual(int(self.fx.cur.fetchone()[0]), 2)

    def test_the_enqueued_row_records_the_approval_and_not_a_credential(self) -> None:
        """The run holds the confirmation id. The bearer token never lands in this table.

        The id route through the gateway applies the identical owner scope, expiry
        predicate, action binding and argument-hash binding as the token route. What it
        does not require is a secret, which means a stolen run row is not an approval.
        """
        granted = self.fx.grant_confirmation("crypto.alerts.pause", {"alert_id": 1})
        run_id = self._enqueue(confirmation_id=granted)
        self.fx.commit()
        self.fx.cur.execute("SELECT * FROM undx_agent_runs WHERE run_id=?", (run_id,))
        row = dict(self.fx.cur.fetchone())
        self.assertEqual(row["confirmation_id"], granted)
        self.assertEqual(row["status"], "queued")
        self.assertEqual(int(row["attempt_count"]), 0)
        columns = set(row)
        for forbidden in ("confirmation_token", "token", "secret"):
            self.assertNotIn(forbidden, columns,
                             "the run table must not have a column that could hold a credential")

    def test_bounds_are_fixed_on_the_row_at_enqueue(self) -> None:
        """``max_attempts`` is written down, not re-read at execution time.

        This is the same rule ``UNDX_PLANNER_DYNAMIC_LIMIT_ESCALATION_ALLOWED`` exists
        to enforce for missions: nothing discovered at runtime may raise a bound that
        was set when the work was authorised.
        """
        run_id = self._enqueue()
        self.fx.commit()
        self.fx.cur.execute("SELECT max_attempts FROM undx_agent_runs WHERE run_id=?", (run_id,))
        self.assertEqual(int(self.fx.cur.fetchone()[0]), 3)


class ClaimingIsExclusiveAndBounded(unittest.TestCase):

    def setUp(self) -> None:
        self.fx = AgentFixture(**RUN_FLAGS).start()
        from services import undx_agent_runs

        self.runs = undx_agent_runs
        self.runs.ensure_schema(self.fx.cur)
        self.run_id = self.runs.enqueue(
            self.fx.cur, user_id=OWNER_ID, capability_id="crypto.alerts.pause",
            arguments={"alert_id": 1},
            confirmation_id=self.fx.grant_confirmation("crypto.alerts.pause",
                                                       {"alert_id": 1}),
            client_request_id="req_1",
        )
        self.fx.commit()

    def tearDown(self) -> None:
        self.fx.stop()

    def test_two_workers_racing_one_run_produce_exactly_one_winner(self) -> None:
        """The compare-and-swap, exercised rather than described.

        Both workers read the same row, both attempt the same conditional update, and
        the second one's ``updated_at`` predicate no longer matches. The loser gets
        ``None`` and moves on; it does not block and it does not wait.
        """
        first = self.runs.claim_next(self.fx.cur, "worker-a")
        second = self.runs.claim_next(self.fx.cur, "worker-b")
        self.assertIsNotNone(first)
        self.assertIsNone(second, "a claimed run must not be claimable again")
        self.assertEqual(first["lease_owner"], "worker-a")
        self.assertEqual(int(first["attempt_count"]), 1)

    def test_a_live_lease_is_not_reclaimed(self) -> None:
        self.runs.claim_next(self.fx.cur, "worker-a")
        self.fx.commit()
        soon = datetime.now(timezone.utc) + timedelta(seconds=30)
        self.assertIsNone(self.runs.claim_next(self.fx.cur, "worker-b", now=soon))

    def test_an_expired_lease_is_reclaimed_so_a_dead_container_strands_nothing(self) -> None:
        """The clock moves past the 120-second lease and stays inside the run deadline.

        Those are two different bounds and this test is about the first one. A run whose
        *approval* has also lapsed must not be reclaimed at all, which is the next test;
        picking a moment that crossed both would have made this one pass or fail for
        either reason.
        """
        self.runs.claim_next(self.fx.cur, "worker-a")
        self.fx.commit()
        later = datetime.now(timezone.utc) + timedelta(seconds=200)
        reclaimed = self.runs.claim_next(self.fx.cur, "worker-b", now=later)
        self.assertIsNotNone(reclaimed)
        self.assertEqual(reclaimed["lease_owner"], "worker-b")
        self.assertEqual(int(reclaimed["attempt_count"]), 2,
                         "a reclaim is an attempt; crash loops are bounded by the same counter")

    def test_a_run_past_its_deadline_is_expired_rather_than_reclaimed(self) -> None:
        """A stale run is not a run to retry. It is a run to stop.

        The deadline is the earlier of the run TTL and the approval's own expiry, so a
        run found past it is one whose confirmation can no longer be redeemed. Retrying
        it would spend an attempt reaching a refusal that was certain before the claim,
        and — worse — would keep an action a person approved five minutes ago alive in a
        queue where it might execute at a moment they no longer intend.
        """
        self.runs.claim_next(self.fx.cur, "worker-a")
        self.fx.commit()
        much_later = datetime.now(timezone.utc) + timedelta(seconds=4000)
        self.assertIsNone(self.runs.claim_next(self.fx.cur, "worker-b", now=much_later))
        self.fx.cur.execute("SELECT status, last_error FROM undx_agent_runs WHERE run_id=?",
                            (self.run_id,))
        row = dict(self.fx.cur.fetchone())
        self.assertEqual(row["status"], "expired")
        self.assertEqual(row["last_error"], "run_deadline_passed")

    def test_the_deadline_never_outlives_the_approval_that_authorised_it(self) -> None:
        """Asserted as an ordering between two stored timestamps rather than as a value.

        Confirmations are clamped to 300 seconds at mint and the run TTL is an hour, so
        today the approval always decides. Writing the assertion this way keeps it true
        if either constant moves, which is the point: the rule is "a run cannot outlive
        its permission", not "a run lasts five minutes".
        """
        self.fx.cur.execute(
            "SELECT r.expires_at AS run_expiry, c.expires_at AS grant_expiry "
            "FROM undx_agent_runs r JOIN pulse_ai_confirmations c "
            "ON c.confirmation_id = r.confirmation_id WHERE r.run_id=?", (self.run_id,))
        row = dict(self.fx.cur.fetchone())
        self.assertTrue(row["run_expiry"])
        self.assertLessEqual(str(row["run_expiry"]), str(row["grant_expiry"]))

    def test_attempts_are_bounded_and_the_run_dead_letters_rather_than_looping(self) -> None:
        """Dead-lettering happens on the claim, before another execution.

        A run that has already used its full allowance has, by definition, possibly
        executed. Attempting it once more is the one move that could turn an uncertain
        single write into a certain double one.
        """
        self.fx.cur.execute(
            "UPDATE undx_agent_runs SET attempt_count=3, status='queued' WHERE run_id=?",
            (self.run_id,),
        )
        self.fx.commit()
        self.assertIsNone(self.runs.claim_next(self.fx.cur, "worker-a"))
        self.fx.cur.execute("SELECT status, last_error FROM undx_agent_runs WHERE run_id=?",
                            (self.run_id,))
        row = dict(self.fx.cur.fetchone())
        self.assertEqual(row["status"], "dead_letter")
        self.assertEqual(row["last_error"], "max_attempts_exhausted")

    def test_a_dead_lettered_run_never_re_enters_the_queue(self) -> None:
        self.fx.cur.execute(
            "UPDATE undx_agent_runs SET status='dead_letter' WHERE run_id=?", (self.run_id,))
        self.fx.commit()
        self.assertIsNone(self.runs.claim_next(self.fx.cur, "worker-a"))

    def test_nothing_is_claimed_while_the_feature_is_off(self) -> None:
        self.fx.set_flags(UNDX_AGENT_RUNS_ENABLED="")
        self.assertIsNone(self.runs.claim_next(self.fx.cur, "worker-a"))

    def test_nothing_is_claimed_during_an_emergency_stop(self) -> None:
        """Checked before the claim, not only inside the gateway.

        The gateway would refuse anyway, but the run would have spent an attempt and a
        lease reaching a refusal that was never in doubt. Stopping at the claim leaves
        the row exactly as it was when the switch was thrown.
        """
        self.fx.set_flags(UNDX_EMERGENCY_KILL_SWITCH="1")
        self.assertIsNone(self.runs.claim_next(self.fx.cur, "worker-a"))
        self.fx.cur.execute("SELECT attempt_count, status FROM undx_agent_runs WHERE run_id=?",
                            (self.run_id,))
        row = dict(self.fx.cur.fetchone())
        self.assertEqual(int(row["attempt_count"]), 0)
        self.assertEqual(row["status"], "queued")


class ExecutionGoesThroughTheOneGateway(unittest.TestCase):
    """No second authority: the run supplies facts, the gateway makes every decision."""

    def setUp(self) -> None:
        self.fx = AgentFixture(**RUN_FLAGS).start()
        from services import undx_agent_runs, undx_tool_gateway

        self.runs = undx_agent_runs
        self.gateway = undx_tool_gateway
        self.runs.ensure_schema(self.fx.cur)
        self.alert_id = self.fx.make_alert(OWNER_ID, symbol="BTC")
        self.fx.commit()

    def tearDown(self) -> None:
        self.fx.stop()

    def _queue(self, **overrides) -> dict:
        payload = {
            "user_id": OWNER_ID,
            "capability_id": "crypto.alerts.pause",
            "arguments": {"alert_id": self.alert_id},
            "client_request_id": "req_exec",
        }
        payload.update(overrides)
        if "confirmation_id" not in payload:
            payload["confirmation_id"] = self.fx.grant_confirmation(
                payload["capability_id"], payload["arguments"],
                user_id=payload["user_id"],
            )
        self.confirmation_id = payload["confirmation_id"]
        self.runs.enqueue(self.fx.cur, **payload)
        self.fx.commit()
        claimed = self.runs.claim_next(self.fx.cur, "worker-a")
        self.assertIsNotNone(claimed, "the fixture run should be claimable")
        self.fx.commit()
        return claimed

    def test_an_approval_that_lapsed_after_queueing_does_not_execute(self) -> None:
        """The lapsed-confirmation case, which is the ordinary one in production.

        A person confirms, closes the app, and the worker is down long enough for the
        approval to expire. The run must end without acting. It must not wait for an
        approval that will never arrive, and it must not act without one.

        The approval is real at enqueue and gone by execution, because that is the
        sequence production produces — an id that never existed is refused at the queue
        now and could not reach this path at all.
        """
        claimed = self._queue()
        self.fx.cur.execute("DELETE FROM pulse_ai_confirmations WHERE confirmation_id=?",
                            (self.confirmation_id,))
        self.fx.commit()
        outcome = self.runs.execute_claimed(self.fx.cur, claimed, "worker-a")
        self.fx.commit()
        self.assertEqual(outcome["status"], "failed")

        from services import alert_engine

        listed = alert_engine.list_alert_rules(OWNER_ID)
        rows = listed if isinstance(listed, list) else listed.get("alerts", [])
        for row in rows:
            if int(row.get("alert_id") or row.get("id") or 0) == self.alert_id:
                self.assertNotEqual(str(row.get("status") or "").lower(), "paused",
                                    "an unapproved run must not have changed anything")

    def test_a_run_whose_lease_was_lost_does_not_execute(self) -> None:
        """A worker that overran its lease must not act on a run somebody else now owns."""
        claimed = self._queue()
        stolen = dict(claimed)
        stolen["lease_owner"] = "worker-b"
        outcome = self.runs.execute_claimed(self.fx.cur, stolen, "worker-a")
        self.assertFalse(outcome["executed"])
        self.assertEqual(outcome["reason"], "lease_not_owned")

    def test_unreadable_arguments_fail_the_run_rather_than_defaulting_to_empty(self) -> None:
        """An empty dict would validate cleanly against a schema of optional fields and
        execute a different action than the one that was approved. Corrupt input is not
        a reason to guess."""
        claimed = self._queue()
        self.fx.cur.execute("UPDATE undx_agent_runs SET arguments_json='{not json' WHERE run_id=?",
                            (claimed["run_id"],))
        self.fx.commit()
        claimed["arguments_json"] = "{not json"
        outcome = self.runs.execute_claimed(self.fx.cur, claimed, "worker-a")
        self.fx.commit()
        self.assertEqual(outcome["reason"], "arguments_unreadable")
        self.fx.cur.execute("SELECT status FROM undx_agent_runs WHERE run_id=?",
                            (claimed["run_id"],))
        self.assertEqual(dict(self.fx.cur.fetchone())["status"], "failed")

    def test_the_worker_calls_the_gateway_and_supplies_no_authority_of_its_own(self) -> None:
        """The load-bearing structural assertion of this whole module.

        Every argument the gateway receives comes off the run row. In particular
        ``target_chosen_by_agent`` is ``False`` because a deterministic resolver chose
        the target in the request — and the policy engine is entitled to rely on that,
        so it must never be asserted from anywhere but the resolver's own path.
        """
        seen: dict = {}
        original = self.gateway.execute

        def capture(cur, **kwargs):
            seen.update(kwargs)
            return original(cur, **kwargs)

        self.gateway.execute = capture
        try:
            claimed = self._queue()
            self.runs.execute_claimed(self.fx.cur, claimed, "worker-a")
        finally:
            self.gateway.execute = original

        self.assertEqual(seen["user_id"], OWNER_ID)
        self.assertEqual(seen["capability_id"], "crypto.alerts.pause")
        self.assertEqual(seen["confirmation_id"], self.confirmation_id)
        self.assertFalse(seen["target_chosen_by_agent"])
        self.assertTrue(seen["explicit_request"])
        self.assertNotIn("confirmation_token", [k for k, v in seen.items() if v],
                         "no bearer token should be presented from storage")
        self.assertEqual(seen["client_request_id"], "req_exec")

    def test_the_request_id_is_stable_across_reclaims_so_a_crash_is_resumable(self) -> None:
        """Resumability is the gateway's idempotency, reached by keeping one key.

        The run id is passed as the request id every time the run is claimed, so a
        second attempt after a crash meets the ledger row the first attempt reserved
        and is told the outcome is unknown, rather than repeating the write.
        """
        claimed = self._queue()
        run_id = claimed["run_id"]
        first: dict = {}

        original = self.gateway.execute

        def capture(cur, **kwargs):
            first.setdefault("request_id", kwargs["request_id"])
            first.setdefault("client_request_id", kwargs["client_request_id"])
            return original(cur, **kwargs)

        self.gateway.execute = capture
        try:
            self.runs.execute_claimed(self.fx.cur, claimed, "worker-a")
            self.fx.cur.execute(
                "UPDATE undx_agent_runs SET status='queued', lease_owner='', "
                "lease_expires_at='', attempt_count=1 WHERE run_id=?", (run_id,))
            self.fx.commit()
            again = self.runs.claim_next(self.fx.cur, "worker-b")
            self.assertIsNotNone(again)
            second: dict = {}

            def capture_again(cur, **kwargs):
                second.setdefault("request_id", kwargs["request_id"])
                second.setdefault("client_request_id", kwargs["client_request_id"])
                return original(cur, **kwargs)

            self.gateway.execute = capture_again
            self.runs.execute_claimed(self.fx.cur, again, "worker-b")
        finally:
            self.gateway.execute = original

        self.assertEqual(first["request_id"], run_id)
        self.assertEqual(second["request_id"], run_id,
                         "a reclaimed run must present the same key, or it is a new write")
        self.assertEqual(first["client_request_id"], second["client_request_id"])


class RunsAreVisibleOnlyToTheirOwner(unittest.TestCase):

    def setUp(self) -> None:
        self.fx = AgentFixture(**RUN_FLAGS).start()
        from services import undx_agent_runs

        self.runs = undx_agent_runs
        self.runs.ensure_schema(self.fx.cur)
        for uid, req in ((OWNER_ID, "req_owner"), (OTHER_ID, "req_other")):
            self.runs.enqueue(
                self.fx.cur, user_id=uid, capability_id="crypto.alerts.pause",
                arguments={"alert_id": 1},
                confirmation_id=self.fx.grant_confirmation(
                    "crypto.alerts.pause", {"alert_id": 1}, user_id=uid),
                client_request_id=req,
            )
        self.fx.commit()

    def tearDown(self) -> None:
        self.fx.stop()

    def test_the_read_back_is_scoped_in_the_statement(self) -> None:
        """Scoped by ``WHERE user_id=?`` rather than filtered afterwards, so there is no
        arrangement of arguments that returns somebody else's row to be discarded later."""
        mine = self.runs.for_user(self.fx.cur, OWNER_ID)
        self.assertEqual(len(mine), 1)
        theirs = self.runs.for_user(self.fx.cur, OTHER_ID)
        self.assertEqual(len(theirs), 1)
        self.assertNotEqual(mine[0]["run_id"], theirs[0]["run_id"])

    def test_the_read_back_carries_no_approval_handle(self) -> None:
        """Status is safe to show. The confirmation id is not presentation, and a client
        that received one could present it to the gateway."""
        for row in self.runs.for_user(self.fx.cur, OWNER_ID):
            self.assertNotIn("confirmation_id", row)
            self.assertNotIn("arguments_json", row)


class TheWorkerLoopStaysOffUntilItIsTurnedOn(unittest.TestCase):

    def setUp(self) -> None:
        self.fx = AgentFixture(**RUN_FLAGS).start()
        from services import undx_agent_runs

        self.runs = undx_agent_runs

    def tearDown(self) -> None:
        self.fx.stop()

    def test_poll_is_inert_and_honest_about_why_when_disabled(self) -> None:
        self.fx.set_flags(UNDX_AGENT_RUNS_ENABLED="")
        result = self.runs.poll_once()
        self.assertFalse(result["enabled"])
        self.assertEqual(result["reason"], "agent_runs_disabled")
        self.assertFalse(result["executed"])

    def test_the_worker_flag_gates_runs_too(self) -> None:
        """A run is worker-executed work. Turning the worker off must turn it off, or
        the two switches mean different things in the same process."""
        self.fx.set_flags(UNDX_WORKER_ENABLED="")
        self.assertFalse(self.runs.surface().enabled)
        self.assertEqual(self.runs.surface().reason, "worker_disabled")

    def test_dynamic_escalation_disables_runs_entirely(self) -> None:
        self.fx.set_flags(UNDX_PLANNER_DYNAMIC_LIMIT_ESCALATION_ALLOWED="1")
        self.assertFalse(self.runs.surface().enabled)
        self.assertEqual(self.runs.surface().reason, "dynamic_limit_escalation_is_unsafe")


if __name__ == "__main__":
    unittest.main()
