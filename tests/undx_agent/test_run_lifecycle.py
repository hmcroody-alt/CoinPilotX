"""The four ways a run can stop, and the two ways it must not.

Stages 18 through 21 are all one question asked at different moments: *what is this run
allowed to say about itself?* A queue that answers that question with two words —
succeeded, failed — is forced to lie about at least one real case, and the cases it lies
about are the ones nobody is watching.

**Stage 18 — a run waiting on a person occupies no worker.** The failure is quiet and
total. A parked run that ``claim_next`` picks up takes a lease and spends one of a fixed
allowance of attempts against an approval that does not exist yet; three passes later it
is dead-lettered, and the person it was waiting on has still not been asked. The guard
belongs in the claim query because that is the only code that can violate it.

**Stage 19 — cancel means cancel, and refuses when it cannot.** A run no worker has taken
stops. A run being executed right now cannot be recalled, and the honest answer to a
cancel request for one is "too late", not "cancelled". The test that matters here is the
one that asserts the *refusal*, because the tempting bug is the courteous one.

**Stage 20 — partial is a real settlement.** ``accepted_unverified`` means the executor
ran and the read-back could not confirm it. Stored as ``failed`` it tells somebody their
block did not happen and invites them to do it again by hand, which is how one uncertain
write becomes two.

**Stage 21 — only a verified success is a success.** Asserted across the whole outcome
enum rather than on the happy path, so the day a new outcome is added the sweep covers it.

The gateway is stubbed here. These tests are about what the queue does with an answer,
not about how the gateway reaches one — that is
``tests/undx_agent/test_run_execution.py``'s subject, and a real gateway call would make
this file test both at once and diagnose neither.
"""

from __future__ import annotations

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

A_READ = "activity.daily_summary"


class _Receipt:
    def __init__(self, status: str) -> None:
        self.status = status
        self.explanation = ""


class _Answer:
    """What ``undx_tool_gateway.execute`` hands back, reduced to the two fields the queue
    reads. Nothing here decides anything; the point of the stub is that the queue's
    settlement is a pure function of these two values.

    **The attribute is named ``succeeded`` because that is what the real object calls it.**
    An earlier version of this stub called it ``may_claim_completed``, a name
    ``GatewayOutcome`` has never carried, and every test in this file passed against it
    while the production settlement rule — reading the same absent name through a
    ``getattr`` default — settled every real run as ``failed``. A stub is a claim about
    somebody else's contract, and a claim nothing checks is how a suite ends up proving a
    property of itself. ``TheStubMatchesTheRealContract`` below is the check.
    """

    def __init__(self, status: str, succeeded: bool) -> None:
        self.receipt = _Receipt(status)
        self.succeeded = succeeded


class LifecycleBase(unittest.TestCase):
    def setUp(self) -> None:
        self.fx = AgentFixture(**RUN_FLAGS).start()
        from services import undx_agent_runs

        self.runs = undx_agent_runs
        self.runs.ensure_schema(self.fx.cur)
        self.fx.commit()

    def tearDown(self) -> None:
        self.fx.stop()

    def _queue(self, *, user_id=OWNER_ID, request_id="req_1", capability_id=A_READ):
        run_id = self.runs.enqueue(
            self.fx.cur, user_id=user_id, capability_id=capability_id, arguments={},
            confirmation_id="", client_request_id=request_id,
        )
        self.fx.commit()
        return run_id

    def _set(self, run_id: str, **columns) -> None:
        """Write columns directly.

        Reaching past the module's own writers on purpose: several of these states —
        a pending confirmation, a lapsed lease — are ones ``enqueue`` correctly refuses to
        create, and the guards under test exist precisely for rows that arrive by some
        other route, including a future one.
        """
        assignments = ", ".join(f"{name}=?" for name in columns)
        self.fx.cur.execute(
            f"UPDATE undx_agent_runs SET {assignments} WHERE run_id=?",
            (*columns.values(), run_id),
        )
        self.fx.commit()

    def _row(self, run_id: str) -> dict:
        self.fx.cur.execute("SELECT * FROM undx_agent_runs WHERE run_id=?", (run_id,))
        return dict(self.fx.cur.fetchone())


class AParkedRunOccupiesNoWorker(LifecycleBase):
    """Stage 18. The claim query passes it over and leaves it untouched."""

    def test_a_run_waiting_on_a_person_is_never_claimed(self) -> None:
        """Swept over every spelling of "waiting", because the guard is a recogniser.

        A recogniser that only knows the spelling in use today fails open the first time
        another module writes a synonym, and failing open here means executing a write
        against an approval nobody granted.
        """
        from services.undx_agent_contracts import RunConfirmation

        for state in sorted(RunConfirmation.PENDING_STATES):
            with self.subTest(state):
                run_id = self._queue(request_id=f"req_{state}")
                self._set(run_id, confirmation_state=state)

                self.assertIsNone(
                    self.runs.claim_next(self.fx.cur, "worker_1"),
                    "a run parked on a person must not be claimable")

    def test_being_passed_over_costs_the_run_nothing(self) -> None:
        """Skipped, not settled and not charged.

        The whole point of Stage 18 is that the person still gets to answer. A guard that
        refused the claim but spent the attempt would run the row out of attempts while it
        waited, which is the original bug wearing a different status.
        """
        run_id = self._queue()
        self._set(run_id, confirmation_state="pending")

        self.runs.claim_next(self.fx.cur, "worker_1")
        row = self._row(run_id)

        self.assertEqual(row["status"], "queued")
        self.assertEqual(int(row["attempt_count"]), 0)
        self.assertEqual(row["lease_owner"], "")

    def test_a_claimable_run_behind_a_parked_one_is_still_reached(self) -> None:
        """The skip is a skip, not a stop.

        Written because the cheap implementation of "don't claim parked runs" is to break
        out of the scan, and the resulting head-of-line block would stall every other
        person's work behind one unanswered confirmation card.
        """
        parked = self._queue(request_id="req_parked")
        self._set(parked, confirmation_state="pending")
        wanted = self._queue(request_id="req_wanted")

        claimed = self.runs.claim_next(self.fx.cur, "worker_1")

        self.assertIsNotNone(claimed)
        self.assertEqual(str(claimed["run_id"]), wanted)

    def test_a_parked_run_still_expires(self) -> None:
        """Waiting on a person is not a licence to wait forever.

        If the deadline did not apply to parked runs they would accumulate in the claim
        window permanently — unanswerable, unclaimable, and occupying the twenty-five rows
        the scan looks at. So expiry is checked ahead of the park guard, and a lapsed
        parked run settles as ``expired`` having executed nothing.
        """
        run_id = self._queue()
        past = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat(timespec="seconds")
        self._set(run_id, confirmation_state="pending", expires_at=past)

        self.runs.claim_next(self.fx.cur, "worker_1")

        self.assertEqual(self._row(run_id)["status"], "expired")

    def test_the_projection_and_the_claim_query_read_one_vocabulary(self) -> None:
        """Not two frozensets that happen to match today.

        Held as separate literals, the drift is silent and one-directional: a run the
        status endpoint calls "waiting for you" that the worker claims and executes
        anyway. Asserting the identity of the object rather than its contents is what
        makes a divergent copy impossible rather than merely detectable.
        """
        from services import undx_run_status
        from services.undx_agent_contracts import RunConfirmation

        self.assertIs(undx_run_status._PENDING_CONFIRMATION,
                      RunConfirmation.PENDING_STATES)

    def test_a_parked_run_reads_as_waiting_rather_than_queued(self) -> None:
        """The two halves of Stage 18, joined. It is not claimed *and* it does not say
        "queued" — which would tell somebody to wait for a worker that is deliberately
        never coming."""
        from services import undx_run_status
        from services.undx_run_status import RunStatus

        run_id = self._queue()
        self._set(run_id, confirmation_state="pending")

        projection = undx_run_status.project(
            self.runs.get_for_user(self.fx.cur, OWNER_ID, run_id))

        self.assertEqual(projection.status, RunStatus.WAITING_CONFIRMATION)
        self.assertFalse(projection.terminal)


class CancelStopsWhatItCanAndSaysSoWhenItCannot(LifecycleBase):
    """Stage 19. Four answers, and the interesting three are the refusals."""

    def test_a_queued_run_cancels_and_stops_being_claimable(self) -> None:
        """Cancellation that leaves the row claimable is not cancellation."""
        run_id = self._queue()

        result = self.runs.cancel_for_user(self.fx.cur, OWNER_ID, run_id)
        self.fx.commit()

        self.assertEqual(result, self.runs.CANCEL_DONE)
        self.assertEqual(self._row(run_id)["status"], "cancelled")
        self.assertIsNone(self.runs.claim_next(self.fx.cur, "worker_1"))

    def test_a_running_run_is_refused_rather_than_pretended_away(self) -> None:
        """The test this class exists for.

        There is no message that reaches inside the gateway and un-sends a request already
        in flight. Answering "cancelled" and letting the write land is the single worst
        outcome available here — worse than refusing, because the person stops watching.
        """
        run_id = self._queue()
        claimed = self.runs.claim_next(self.fx.cur, "worker_1")
        self.fx.commit()
        self.assertEqual(str(claimed["run_id"]), run_id)

        result = self.runs.cancel_for_user(self.fx.cur, OWNER_ID, run_id)
        self.fx.commit()

        self.assertEqual(result, self.runs.CANCEL_IN_FLIGHT)
        self.assertEqual(self._row(run_id)["status"], "running")

    def test_a_run_whose_lease_lapsed_is_still_refused(self) -> None:
        """The tempting case, and the one where cancelling is least safe.

        A dead lease looks abandoned. It is equally consistent with a container that died
        *after* the executor returned and before the outcome was written — in which case
        the write landed and "cancelled" is a false statement about the account. Nothing
        here can tell those apart, so both are refused and the reclaim path settles them
        on the gateway's evidence instead.
        """
        run_id = self._queue()
        self.runs.claim_next(self.fx.cur, "worker_1")
        past = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat(timespec="seconds")
        self._set(run_id, lease_expires_at=past)

        self.assertEqual(self.runs.cancel_for_user(self.fx.cur, OWNER_ID, run_id),
                         self.runs.CANCEL_IN_FLIGHT)

    def test_a_finished_run_reports_that_it_already_finished(self) -> None:
        """Distinct from ``not_found``, because "it already happened" and "there is no such
        thing" call for different sentences and only one of them is reassuring."""
        run_id = self._queue()
        self._set(run_id, status="succeeded", outcome="verified_success")

        self.assertEqual(self.runs.cancel_for_user(self.fx.cur, OWNER_ID, run_id),
                         self.runs.CANCEL_ALREADY_SETTLED)

    def test_another_account_cannot_cancel_your_run(self) -> None:
        """Owner in the ``UPDATE``, not checked around it.

        Asserted on the row rather than only on the return code: a cancel that answered
        ``not_found`` while still writing ``status='cancelled'`` would pass a
        return-value-only test and destroy somebody's queued work.
        """
        run_id = self._queue(user_id=OWNER_ID)

        result = self.runs.cancel_for_user(self.fx.cur, OTHER_ID, run_id)
        self.fx.commit()

        self.assertEqual(result, self.runs.CANCEL_NOT_FOUND)
        self.assertEqual(self._row(run_id)["status"], "queued")

    def test_an_unknown_and_a_foreign_run_are_the_same_answer(self) -> None:
        """Same reason as the detail endpoint: distinguishing them would confirm the
        existence of other people's runs to anybody willing to guess ids."""
        foreign = self._queue(user_id=OWNER_ID)

        self.assertEqual(
            self.runs.cancel_for_user(self.fx.cur, OTHER_ID, foreign),
            self.runs.cancel_for_user(self.fx.cur, OTHER_ID, "run_nope"),
        )

    def test_cancelling_twice_does_not_claim_to_have_cancelled_twice(self) -> None:
        """The second call changed nothing and says so. Both answers agree the run will
        not execute, which is what a client actually needs."""
        run_id = self._queue()
        self.runs.cancel_for_user(self.fx.cur, OWNER_ID, run_id)
        self.fx.commit()

        self.assertEqual(self.runs.cancel_for_user(self.fx.cur, OWNER_ID, run_id),
                         self.runs.CANCEL_ALREADY_SETTLED)

    def test_a_cancelled_run_reads_as_cancelled_and_terminal(self) -> None:
        from services import undx_run_status
        from services.undx_run_status import RunStatus

        run_id = self._queue()
        self.runs.cancel_for_user(self.fx.cur, OWNER_ID, run_id)
        self.fx.commit()

        projection = undx_run_status.project(
            self.runs.get_for_user(self.fx.cur, OWNER_ID, run_id))

        self.assertEqual(projection.status, RunStatus.CANCELLED)
        self.assertTrue(projection.terminal)
        self.assertFalse(projection.may_claim_completed)


class OnlyAVerifiedSuccessIsASuccess(LifecycleBase):
    """Stages 20 and 21, which are the same rule read from its two ends."""

    def _settle(self, status_word: str, succeeded: bool) -> str:
        """Settle one claimed run against a stubbed gateway answer, return the stored
        status."""
        from services import undx_agent_runs, undx_tool_gateway

        run_id = self._queue(request_id=f"req_{status_word}_{succeeded}")
        claimed = undx_agent_runs.claim_next(self.fx.cur, "worker_1")
        self.fx.commit()

        original = undx_tool_gateway.execute
        undx_tool_gateway.execute = (
            lambda *a, **k: _Answer(status_word, succeeded))
        try:
            undx_agent_runs.execute_claimed(self.fx.cur, claimed, "worker_1")
            self.fx.commit()
        finally:
            undx_tool_gateway.execute = original
        return self._row(run_id)["status"]

    def test_no_outcome_but_a_licensed_completion_reaches_succeeded(self) -> None:
        """Stage 21, swept across the whole enum.

        The sweep is the assertion. A happy-path test proves ``verified_success`` works;
        this proves nothing *else* does, which is the property that has to survive somebody
        adding an eleventh outcome.
        """
        from services.undx_agent_contracts import AgentOutcome

        for outcome in sorted(AgentOutcome.ALL):
            with self.subTest(outcome):
                stored = self._settle(outcome, succeeded=False)
                self.assertNotEqual(
                    stored, "succeeded",
                    "no outcome may settle as succeeded without the gateway's own "
                    "completion licence")

    def test_the_completion_licence_is_taken_from_the_gateway_not_the_word(self) -> None:
        """``succeeded`` is reached through the gateway's own verdict and nothing else.

        Not by comparing the outcome string here. A second copy of "may we say done" is a
        second thing to keep correct, and the two would disagree the first time either
        moved.
        """
        from services.undx_agent_contracts import AgentOutcome

        self.assertEqual(
            self._settle(AgentOutcome.VERIFIED_SUCCESS, succeeded=True), "succeeded")

    def test_an_unverified_execution_settles_partial_rather_than_failed(self) -> None:
        """Stage 20, stated as the harm it prevents.

        ``accepted_unverified`` means the executor ran and the read-back could not confirm
        it. Recorded as ``failed``, the person is told their block did not happen and does
        it again by hand — turning one uncertain write into two certain ones.
        """
        from services.undx_agent_contracts import AgentOutcome

        self.assertEqual(
            self._settle(AgentOutcome.ACCEPTED_UNVERIFIED, succeeded=False), "partial")

    def test_only_an_unverified_execution_reaches_partial(self) -> None:
        """The rounding is unavailable in both directions.

        Every other non-success outcome is a refusal that happened *before* an executor
        ran — permission denied, unsupported capability, a lapsed confirmation — and
        calling any of those partial would claim an execution that never occurred.
        """
        from services.undx_agent_contracts import AgentOutcome

        for outcome in sorted(AgentOutcome.ALL - {AgentOutcome.ACCEPTED_UNVERIFIED}):
            with self.subTest(outcome):
                self.assertEqual(self._settle(outcome, succeeded=False), "failed")

    def test_partial_is_terminal_and_is_never_retried(self) -> None:
        """A retry would be a second execution of an action that may have landed.

        The uncertainty is the finding. Repeating the write to resolve it is the one move
        guaranteed to make it worse, which is why ``partial`` sits in
        ``TERMINAL_STATUSES`` beside ``succeeded`` rather than beside ``queued``.
        """
        run_id = self._queue()
        self._set(run_id, status="partial", outcome="accepted_unverified")

        self.assertIn("partial", self.runs.TERMINAL_STATUSES)
        self.assertIsNone(self.runs.claim_next(self.fx.cur, "worker_1"))

    def test_a_partial_row_projects_as_partial_and_claims_nothing(self) -> None:
        """And cannot be promoted out of it.

        The status is not re-derived from the outcome here — the queue already decided —
        so even a row whose outcome would otherwise read as licensed stays ``PARTIAL``.
        Nothing is promoted.
        """
        from services import undx_run_status
        from services.undx_run_status import RunStatus

        run_id = self._queue()
        self._set(run_id, status="partial", outcome="verified_success")

        projection = undx_run_status.project(
            self.runs.get_for_user(self.fx.cur, OWNER_ID, run_id))

        self.assertEqual(projection.status, RunStatus.PARTIAL)
        self.assertTrue(projection.terminal)
        self.assertFalse(projection.may_claim_completed)

    def test_a_partial_run_records_why_it_could_not_be_confirmed(self) -> None:
        """An empty error field on a settled run reads as "nothing went wrong", which is
        the one thing a partial is not saying."""
        from services.undx_agent_contracts import AgentOutcome

        self._settle(AgentOutcome.ACCEPTED_UNVERIFIED, succeeded=False)
        self.fx.cur.execute(
            "SELECT last_error, outcome FROM undx_agent_runs WHERE status='partial'")
        row = dict(self.fx.cur.fetchone())

        self.assertEqual(row["outcome"], AgentOutcome.ACCEPTED_UNVERIFIED)
        self.assertTrue(row["last_error"])


class TheCancelRouteActsAndDoesNotRead(unittest.TestCase):
    """One POST, and it is not on the polling surface."""

    def test_it_registers_exactly_the_one_documented_write_route(self) -> None:
        from flask import Flask
        from services import undx_agent_run_control_routes as pack

        app = Flask(__name__)
        pack.register(app)
        undx = {str(rule): sorted(rule.methods - {"HEAD", "OPTIONS"})
                for rule in app.url_map.iter_rules() if "/api/undx/" in str(rule)}

        self.assertEqual(undx, {"/api/undx/runs/<run_id>/cancel": ["POST"]})

    def test_the_read_pack_stays_free_of_it(self) -> None:
        """The reason the two packs are separate, asserted rather than trusted.

        A client polls the read pack every couple of seconds. Its guarantee is that
        nothing there can mutate, and that guarantee is only worth having while it is
        checkable — so the cancel route living elsewhere is a property of the code, not a
        convention somebody remembers.
        """
        from flask import Flask
        from services import undx_agent_run_routes as read_pack

        app = Flask(__name__)
        read_pack.register(app)
        methods = {method for rule in app.url_map.iter_rules()
                   for method in rule.methods if "/api/undx/" in str(rule)}

        self.assertNotIn("POST", methods)
        self.assertNotIn("DELETE", methods)

    def test_no_identity_or_target_is_taken_from_the_client(self) -> None:
        """Stage 15 applies to the write route more than to the read ones."""
        import inspect
        from services import undx_agent_run_control_routes as pack

        source = inspect.getsource(pack)
        for forbidden in ("request.args.get(\"user_id\")", "request.json", "get_json",
                          "request.form"):
            with self.subTest(forbidden):
                self.assertNotIn(forbidden, source)

    def test_every_cancel_result_has_an_answer_and_none_of_them_is_a_success(self) -> None:
        """The map is total, and only the one that cancelled something returns 200.

        Written as a sweep over the module's own constants so that a fifth result code
        added later fails here rather than falling through to a 500 in front of a person.
        """
        from services import undx_agent_run_control_routes as pack
        from services import undx_agent_runs

        codes = {undx_agent_runs.CANCEL_DONE, undx_agent_runs.CANCEL_NOT_FOUND,
                 undx_agent_runs.CANCEL_ALREADY_SETTLED,
                 undx_agent_runs.CANCEL_IN_FLIGHT}

        self.assertEqual(set(pack._ANSWERS), codes)
        for code, (status, message) in pack._ANSWERS.items():
            with self.subTest(code):
                self.assertTrue(message)
                self.assertEqual(status == 200, code == undx_agent_runs.CANCEL_DONE)


class TheStubMatchesTheRealContract(unittest.TestCase):
    """The check that would have caught the defect the rest of this file slept through.

    Every settlement test above runs against ``_Answer``. That is the right trade — a real
    gateway call would test two subsystems and diagnose neither — but it buys speed with a
    promise: that ``_Answer`` has the shape ``undx_tool_gateway.execute`` actually returns.
    Nothing checked the promise, and it was false. ``_Answer`` carried
    ``may_claim_completed``; ``GatewayOutcome`` never has. The production settlement rule
    read the same absent name through a ``getattr`` default, so it silently answered "not a
    success" for every run the queue ever settled, and this file reported green throughout.

    Two assertions, because the stub can drift in two directions: a field the real object
    lacks, and a field the real object has that the queue reads and the stub omits.
    """

    def test_every_field_the_stub_offers_exists_on_the_real_outcome(self) -> None:
        from services.undx_tool_gateway import GatewayOutcome

        stub = _Answer("verified_success", True)
        real = set(GatewayOutcome.__slots__) | {
            name for name in dir(GatewayOutcome) if not name.startswith("_")}

        for field in vars(stub):
            with self.subTest(field):
                self.assertIn(
                    field, real,
                    f"the stub offers {field!r}, which GatewayOutcome does not carry; a "
                    f"test written against it proves a property of the stub alone")

    def test_the_settlement_rule_reads_only_fields_the_real_outcome_carries(self) -> None:
        """``_settled_status`` against the real class, not against a convenient stand-in.

        Asserted as a property of the *class* rather than by grepping the source, so it
        holds however the rule is spelled. ``GatewayOutcome`` uses ``__slots__``, which
        makes ``succeeded`` impossible to add by accident at runtime — the absence this
        guards is permanent, not a matter of one code path forgetting to set it.
        """
        from services.undx_tool_gateway import GatewayOutcome

        self.assertTrue(
            hasattr(GatewayOutcome, "succeeded"),
            "the queue settles on GatewayOutcome.succeeded; if that property is renamed "
            "the rename has to reach services/undx_agent_runs.py in the same commit")
        self.assertFalse(
            hasattr(GatewayOutcome, "may_claim_completed"),
            "this name has never existed on GatewayOutcome. If it is being added, the "
            "docstring in _settled_status explaining why the queue stopped reading it "
            "needs revisiting before this assertion is deleted")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
