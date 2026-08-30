"""One real read, through the real gateway, settled by the real queue.

``tests/undx_agent/test_run_lifecycle.py`` stubs the gateway on purpose: it is about what
the queue does with an answer, not about how an answer is reached. This file is the other
half, and it exists because the seam between the two is where the defect lived.

**What went wrong.** ``_settled_status`` asked the gateway outcome for
``may_claim_completed``. ``GatewayOutcome`` has no such attribute — its ``__slots__`` are
``receipt``, ``confirmation``, ``result``, ``verification`` and ``is_write`` — so the
``getattr`` default answered "no" every time and no run of any kind could reach
``succeeded``. Underneath that sat a second fault: ``may_claim_completed`` is a *write*
predicate, requiring an independent read-back that verified. A read-only capability
declares no verifier, so its receipt truthfully records ``impossible_to_verify``, and
holding a lookup to a write's evidence standard fails it for a reason that can never be
satisfied. Every capability in ``WORKER_ELIGIBLE_CAPABILITIES`` is a read.

The two faults compounded into one user-visible lie: a summary that was queued, claimed,
executed and returned rows was stored as ``failed`` and projected to the client as
``This did not happen.``

Neither fault was reachable by a test that stubbed the gateway, because a stub is a claim
about a contract and both faults *were* the contract being wrong. Hence: no stubs here.
"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from tests.undx_agent import bootstrap

bootstrap.install()

from tests.undx_agent.harness import AgentFixture, OWNER_ID  # noqa: E402


RUN_FLAGS = {
    "UNDX_AGENT_ENABLED": "1",
    "UNDX_AGENT_READS_ENABLED": "1",
    "UNDX_AGENT_RUNS_ENABLED": "1",
    "UNDX_WORKER_ENABLED": "1",
    "UNDX_BRAIN_ENABLED": "1",
}

#: A read the worker is actually eligible to run, rather than a convenient one. Picking a
#: capability outside ``WORKER_ELIGIBLE_CAPABILITIES`` would prove the path works for
#: something the worker never sees.
A_READ = "activity.daily_summary"

#: The tables ``activity.daily_summary`` reads that the shared harness does not create.
#: Empty rather than absent: an absent table makes the read degrade, which settles
#: ``partial`` — correct behaviour, and not the thing under test here.
EXTRA_DDL = (
    "CREATE TABLE IF NOT EXISTS pulse_messages (id INTEGER PRIMARY KEY, "
    "conversation_id INTEGER, sender_user_id INTEGER, recipient_user_id INTEGER, "
    "body TEXT, created_at TEXT)",
    "CREATE TABLE IF NOT EXISTS pulse_statuses (id INTEGER PRIMARY KEY, user_id INTEGER, "
    "created_at TEXT, deleted_at TEXT)",
    "CREATE TABLE IF NOT EXISTS pulse_conversation_participants (id INTEGER PRIMARY KEY, "
    "conversation_id INTEGER, user_id INTEGER)",
    "CREATE TABLE IF NOT EXISTS pulse_reels (id INTEGER PRIMARY KEY, user_id INTEGER, "
    "status TEXT DEFAULT 'active', created_at TEXT)",
)

#: Columns the read wants on tables the shared harness already created.
#:
#: ``CREATE TABLE IF NOT EXISTS`` is silent about a table that exists with the wrong
#: shape, so the harness's ``pulse_follows`` — which has no ``created_at`` — survived
#: the DDL above untouched and the read degraded on it. A degraded source settles
#: ``partial``, which is correct behaviour and precisely the wrong thing for this file
#: to be measuring: the defect under test is a *clean* read being written down as a
#: failure. The ``ALTER`` is additive and confined to this fixture; the shared harness
#: is left alone because other suites assert against its shape.
EXTRA_COLUMNS = (
    ("pulse_follows", "created_at", "TEXT"),
)


class ExecutionBase(unittest.TestCase):
    def setUp(self) -> None:
        self.fx = AgentFixture(**RUN_FLAGS).start()
        from services import undx_agent_runs

        self.runs = undx_agent_runs
        self.runs.ensure_schema(self.fx.cur)
        self.fx.ensure_feed_schema()
        for ddl in EXTRA_DDL:
            self.fx.cur.execute(ddl)
        for table, column, kind in EXTRA_COLUMNS:
            existing = {row[1] for row in
                        self.fx.cur.execute(f"PRAGMA table_info({table})").fetchall()}
            if column not in existing:
                self.fx.cur.execute(f"ALTER TABLE {table} ADD COLUMN {column} {kind}")
        self.fx.commit()

    def tearDown(self) -> None:
        self.fx.stop()

    def _row(self, run_id: str) -> dict:
        self.fx.cur.execute("SELECT * FROM undx_agent_runs WHERE run_id=?", (run_id,))
        return dict(self.fx.cur.fetchone())

    def _run_one(self, capability_id: str = A_READ, request_id: str = "req_exec") -> dict:
        """Enqueue, claim and execute one run against the unstubbed gateway."""
        run_id = self.runs.enqueue(
            self.fx.cur, user_id=OWNER_ID, capability_id=capability_id, arguments={},
            confirmation_id="", client_request_id=request_id,
        )
        self.fx.commit()
        claimed = self.runs.claim_next(self.fx.cur, "worker_1")
        self.assertIsNotNone(claimed, "the run should have been claimable")
        self.runs.execute_claimed(self.fx.cur, claimed, "worker_1")
        self.fx.commit()
        return self._row(run_id)


class AReadThatRanIsRecordedAsHavingRun(ExecutionBase):
    """The regression. Asserted on the stored row, which is what a person is shown."""

    def test_a_successful_read_settles_succeeded(self) -> None:
        row = self._run_one()

        self.assertEqual(
            row["outcome"], "verified_success",
            "the gateway's own verdict on this read")
        self.assertEqual(
            row["status"], "succeeded",
            "a read that the gateway called verified_success must not be stored as a "
            "failure; this is the defect the Stage 25 QA run exposed")

    def test_the_run_carries_no_error_text(self) -> None:
        """``last_error`` is the field a support engineer reads first.

        Before the fix it held ``verified_success`` — the outcome word, copied into the
        error column because the settlement had decided this was not a success. A row that
        says ``failed`` in one column and ``verified_success`` in the next is not a small
        cosmetic problem; it is the audit trail disagreeing with itself.
        """
        self.assertEqual(self._run_one()["last_error"] or "", "")

    def test_the_client_projection_does_not_say_it_never_happened(self) -> None:
        from services import undx_run_status
        from services.undx_run_status import RunStatus

        projection = undx_run_status.project(self._run_one())

        self.assertEqual(projection.status, RunStatus.COMPLETED)
        self.assertTrue(projection.terminal)
        self.assertNotIn("did not happen", projection.description.lower())

    def test_the_projection_does_not_claim_a_read_back_that_never_ran(self) -> None:
        """The second untruth, uncovered by fixing the first.

        ``COMPLETED``'s stock sentence is "Done, and confirmed by a separate read of your
        account." No read-only capability does that: it declares no verifier, so the
        receipt records ``impossible_to_verify`` and nothing was checked twice. The
        sentence was unreachable while every run settled ``failed``, so widening the read
        path is what made it visible — and inventing a verification is the same kind of
        false statement as the ``failed`` it replaced, facing the other way.
        """
        from services import undx_run_status

        description = undx_run_status.project(self._run_one()).description

        self.assertNotIn("separate read", description.lower())
        self.assertIn("changed nothing", description.lower())

    def test_a_completed_read_still_may_not_claim_a_completion(self) -> None:
        """The fix widens one thing and must widen nothing else.

        ``succeeded`` on the row says the call did what it was asked.
        ``may_claim_completed`` on the projection says a *change* may be announced, and a
        lookup changed nothing — the Brain assesses it ``RETRIEVED``. These two travelling
        together is the whole point of keeping them as separate booleans; if fixing the
        first had moved the second, the fix would have bought a working read path at the
        price of the system being able to say "done" about a read.
        """
        from services import undx_run_status

        projection = undx_run_status.project(self._run_one())

        self.assertFalse(projection.may_claim_completed)


class TheGatewayAnswersTheQuestionItsPropertyNames(ExecutionBase):
    """``GatewayOutcome.succeeded`` asked directly, without the queue in between."""

    def _execute(self, capability_id: str = A_READ):
        from services import undx_tool_gateway

        return undx_tool_gateway.execute(
            self.fx.cur, user_id=OWNER_ID, capability_id=capability_id,
            proposed_arguments={}, request_id="req_direct", explicit_request=True,
        )

    def test_a_read_with_no_verifier_still_counts_as_having_succeeded(self) -> None:
        """The precise pair that used to be contradictory.

        The receipt says the read verified as far as a read can be verified, and says its
        verification state is ``impossible`` because the capability declares no read-back
        path. Both are true. ``succeeded`` used to require the second to say ``verified``,
        which no read can ever produce.
        """
        from services.undx_agent_contracts import AgentOutcome, VerificationState

        outcome = self._execute()

        self.assertFalse(outcome.is_write)
        self.assertEqual(outcome.receipt.status, AgentOutcome.VERIFIED_SUCCESS)
        self.assertEqual(outcome.receipt.verification_state,
                         VerificationState.IMPOSSIBLE)
        self.assertTrue(outcome.succeeded)

    def test_it_is_still_not_a_licence_to_announce_a_change(self) -> None:
        self.assertFalse(self._execute().may_claim_done)

    def test_the_write_reading_is_untouched(self) -> None:
        """The asymmetry may only widen reads.

        Swept over the whole outcome enum against a synthetic receipt, because the safety
        argument for the fix is exactly this: a write's answer is the receipt's answer,
        unchanged, whatever the outcome and whatever the read-back said.
        """
        from services.undx_agent_contracts import (AgentOutcome, AgentReceipt,
                                                   VerificationState)
        from services.undx_tool_gateway import GatewayOutcome

        for status in sorted(AgentOutcome.ALL):
            for verdict in sorted(VerificationState.ALL):
                with self.subTest(status=status, verification=verdict):
                    receipt = AgentReceipt(
                        task_id="t", request_id="r", capability_id=A_READ,
                        action="sweep", status=status, owner_user_id=OWNER_ID,
                        verification_state=verdict)
                    outcome = GatewayOutcome(receipt, is_write=True)
                    self.assertEqual(outcome.succeeded, receipt.may_claim_completed)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
