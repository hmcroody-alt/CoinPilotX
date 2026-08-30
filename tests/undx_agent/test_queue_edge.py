"""The edge where a person's request becomes a durable run.

Everything upstream of this file was infrastructure that nothing called. These tests are
about the call: a sentence arrives, the runtime decides the work should outlive the
request, and a row appears that a worker in another container can claim.

Two properties are load-bearing and both are asserted against the database rather than
against the response:

*Nothing is queued that was not deliberately made queueable.* The default is a
synchronous turn, and it stays the default when the flags are off, when the capability is
not on the eligible list, and when the client sent no idempotency anchor. Each of those is
its own test, because a single "it did not queue" assertion would pass for the wrong
reason as easily as the right one.

*A queued run claims nothing.* ``accepted_queued`` exists precisely so that a row sitting
in a queue cannot render under the client's receipt kicker. The tests check the response
the native client will actually parse — component, verification state, completion claim —
not the server's private opinion of what it did.
"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from tests.undx_agent import bootstrap

bootstrap.install()

from tests.undx_agent.harness import AgentFixture, OTHER_ID, OWNER_ID  # noqa: E402

#: A read that is on the worker-eligible list and that the deterministic resolver reaches
#: from an ordinary sentence. Both halves matter: a capability the resolver cannot reach
#: would make these tests exercise ``_act`` through a back door the product does not have.
QUEUEABLE_TEXT = "what happened today"
QUEUEABLE_CAPABILITY = "activity.daily_summary"

#: Runs are off by default everywhere, including in the harness. Queueing tests turn them
#: on explicitly so that the "off" case is a real assertion elsewhere rather than an
#: accident of fixture defaults.
RUNS_ON = {"UNDX_WORKER_ENABLED": "1", "UNDX_AGENT_RUNS_ENABLED": "1"}


class QueueEdge(unittest.TestCase):
    """Base: runs enabled, one alert seeded, and a helper that speaks to the runtime."""

    FLAGS: dict[str, str] = RUNS_ON

    def setUp(self) -> None:
        self.fx = AgentFixture(**self.FLAGS).start()
        from services import undx_agent_runs, undx_agent_runtime

        self.runtime = undx_agent_runtime
        self.runs = undx_agent_runs

    def tearDown(self) -> None:
        self.fx.stop()

    def say(self, text: str = QUEUEABLE_TEXT, **kwargs):
        response = self.runtime.handle(self.fx.cur, user_id=OWNER_ID, text=text, **kwargs)
        self.fx.commit()
        return response

    def rows(self, user_id: int | None = None) -> list[dict]:
        """Every run row, read straight from the table.

        Not through :func:`for_user`, which hides columns on purpose. A test asserting
        that the envelope is complete has to be able to see the envelope.
        """
        self.runs.ensure_schema(self.fx.cur)
        if user_id is None:
            self.fx.cur.execute("SELECT * FROM undx_agent_runs ORDER BY created_at")
        else:
            self.fx.cur.execute(
                "SELECT * FROM undx_agent_runs WHERE user_id=? ORDER BY created_at",
                (int(user_id),),
            )
        return [dict(row) for row in self.fx.cur.fetchall()]


class RunsDisabled(QueueEdge):
    """The deployment as it stands today: the worker flags are off.

    Its own class rather than a nested fixture inside an enabled one. The harness applies
    flags to ``os.environ`` and only clears the keys it is handed, so a second fixture
    built inside an enabled test inherits the enabling flags and the test passes while
    asserting nothing. That is precisely the shape of false green this suite is meant to
    catch elsewhere, so it must not be the shape of the suite itself.
    """

    FLAGS = {"UNDX_WORKER_ENABLED": "", "UNDX_AGENT_RUNS_ENABLED": ""}

    def test_with_runs_disabled_the_turn_answers_in_the_request(self):
        """A queue nothing claims from is worse than no queue.

        With the worker off, a queued row would read to the person as an action that was
        accepted and then silently never happened. Answering in the request is both the
        honest and the working answer, and it is what the deployment does today.
        """
        response = self.say(client_request_id="tap_1")
        self.assertNotEqual(response.status, "accepted_queued")
        self.assertEqual(self.rows(), [])

    def test_the_dispatch_decision_names_the_flag_rather_than_the_capability(self):
        """The reason code is part of the contract, because "why was this not queued?" is
        asked about individual turns and a wrong-but-plausible answer sends the reader to
        the wrong file."""
        from services import undx_capability_registry, undx_worker_dispatch

        spec = undx_capability_registry.get(QUEUEABLE_CAPABILITY)
        decision = undx_worker_dispatch.decide(spec)
        self.assertTrue(decision.synchronous)
        self.assertEqual(decision.reason, "worker_disabled")


class NothingIsQueuedByAccident(QueueEdge):
    """The remaining ways a turn stays in the request, each for its own stated reason."""

    def test_a_capability_nobody_opted_in_is_not_queued(self):
        """Eligibility is an enumerated set, not an inference.

        Listing alerts is fast and always was. If it began queueing because it happens to
        be a read, the honest status model would become theatre laid over work that was
        never deferred.
        """
        response = self.say("show me my alerts", client_request_id="tap_1")
        self.assertNotEqual(response.status, "accepted_queued")
        self.assertEqual(self.rows(), [])

    def test_without_a_client_request_id_nothing_is_queued(self):
        """No idempotency anchor, no durable run.

        A run keyed on a server-generated id would be a *new* run every time the phone
        retried the same tap. Executed in the request, a duplicate is at worst a duplicate
        the person is present to see.
        """
        response = self.say(QUEUEABLE_TEXT)
        self.assertNotEqual(response.status, "accepted_queued")
        self.assertEqual(self.rows(), [])

    def test_no_write_is_currently_queueable(self):
        """Stated as a test so that adding one has to come here and say so.

        There is no way to carry an approval reference from a request onto a run row yet
        — the runtime holds a confirmation *token* and nothing translates it to the
        confirmation *id* the envelope binds. Until that exists, a write on the eligible
        list would be a run that either executes unconfirmed or never executes at all.
        """
        from services import undx_capability_registry, undx_worker_dispatch

        writes = sorted(
            capability_id
            for capability_id in undx_worker_dispatch.WORKER_ELIGIBLE_CAPABILITIES
            if getattr(undx_capability_registry.get(capability_id), "is_write", False)
        )
        self.assertEqual(writes, [], f"worker-eligible writes with no approval path: {writes}")


class AQueuedRunClaimsNothing(QueueEdge):
    """What the native client is handed when work is deferred."""

    def test_the_response_says_accepted_queued_and_draws_as_progress(self):
        """The card the shipped client will parse.

        ``action_progress`` already exists in ``mobile-native/src/undx/actionCards.ts``
        and is classified as progress under the kicker "IN PROGRESS", so this is
        renderable by a client that predates the server change. It is deliberately not
        ``spec.result_card``: the capability's own card is a receipt, and a receipt for
        something that has not run is the one claim this system exists to refuse.
        """
        response = self.say(client_request_id="tap_1")
        self.assertEqual(response.status, "accepted_queued")
        self.assertTrue(response.handled)
        self.assertEqual(response.card["component"], "action_progress")
        self.assertEqual(response.card["capability_id"], QUEUEABLE_CAPABILITY)
        self.assertTrue(response.card["run_id"].startswith("run_"))

    def test_a_queued_run_may_not_claim_completion(self):
        """The property the whole verification chain exists to protect, at the one route
        the verification chain cannot see.

        Nothing has been attempted. ``verified`` is False and the verification state is
        *pending* rather than *impossible*: there is no read-back to report, and the
        difference between "cannot be confirmed" and "has not been confirmed yet" is the
        difference between a permanent verdict and a job that has not started.
        """
        response = self.say(client_request_id="tap_1")
        self.assertFalse(response.card["verified"])
        self.assertEqual(response.receipt.verification_state, "verification_pending")
        self.assertFalse(response.receipt.may_claim_completed)

    def test_the_brain_reads_a_queued_run_as_proposed_not_executed(self):
        """The second reader of the same outcome must agree with the first.

        ``services.undx_brain.evidence`` decides independently what a turn may claim. If
        it mapped ``accepted_queued`` into the executed family, a queued row would license
        "your summary is ready" through a path the response contract never touches.
        """
        from services.undx_brain import evidence

        assessment = evidence.derive("accepted_queued", None, is_write=False)
        self.assertEqual(assessment.state, "proposed")
        self.assertFalse(assessment.may_claim_done)


class TheEnvelopeIsComplete(QueueEdge):
    """Stage 3: the row carries everything an execution in another process needs."""

    def test_every_envelope_field_is_populated_from_the_registry(self):
        """None of this may come from the caller.

        A caller that could supply the canonical target could name a different row than
        the one the arguments act on, and every downstream reader — the confirmation
        binding, the idempotency key, the audit trail — would agree with the caller
        against the action. So each field is checked for being *present*, and the ones
        with a knowable value are checked against the source that owns it.
        """
        response = self.say(client_request_id="tap_1")
        row = self.rows()[0]

        self.assertEqual(row["run_id"], response.card["run_id"])
        self.assertEqual(row["user_id"], OWNER_ID)
        self.assertEqual(row["capability_id"], QUEUEABLE_CAPABILITY)
        self.assertEqual(row["client_request_id"], "tap_1")
        self.assertEqual(row["status"], "queued")
        self.assertEqual(row["envelope_version"], self.runs.ENVELOPE_VERSION)
        self.assertEqual(row["confirmation_state"], self.runs.CONFIRMATION_NOT_REQUIRED)
        self.assertEqual(row["dispatch_reason"], "long_running_capability")
        self.assertEqual(row["registry_version"], self.runs.registry_version())
        for field in ("arguments_hash", "idempotency_key", "policy_version", "expires_at"):
            self.assertTrue(row[field], f"envelope field {field} is empty")

    def test_the_arguments_hash_is_the_fingerprint_the_gateway_will_check(self):
        """Computed here by the same function that binds an approval.

        Recomputed in the test from the module that owns it rather than copied as a
        literal, so a change to the hashing rule fails this test instead of silently
        making every stored fingerprint incomparable to every new one.
        """
        from services import undx_architecture

        self.say(client_request_id="tap_1")
        row = self.rows()[0]
        import json

        expected = undx_architecture.argument_hash(json.loads(row["arguments_json"]))
        self.assertEqual(row["arguments_hash"], expected)

    def test_a_queued_run_carries_a_deadline_it_cannot_outlive(self):
        """A row with no expiry is a row a worker may claim forever."""
        self.say(client_request_id="tap_1")
        row = self.rows()[0]
        self.assertGreater(row["expires_at"], row["created_at"])


class TheSameTapTwice(QueueEdge):
    """Stage 17, at the enqueue end: a retry must not become a second job."""

    def test_a_repeated_request_returns_the_run_that_already_exists(self):
        """The phone retries a request whose response it never saw.

        The first run is still queued and will still execute, so a second row would be
        the same action performed twice, out of sight, with the person having asked once.
        Returning the existing run is not a consolation answer — it is what actually
        happened.
        """
        first = self.say(client_request_id="tap_1")
        second = self.say(client_request_id="tap_1")
        self.assertEqual(second.status, "accepted_queued")
        self.assertEqual(second.card["run_id"], first.card["run_id"])
        self.assertEqual(len(self.rows()), 1)

    def test_a_reused_request_id_for_different_work_is_refused_not_answered(self):
        """A collision is not a retry.

        Handing back the earlier run would report progress on an action nobody asked for
        the second time. Checked at the module rather than through a sentence, because
        the runtime derives ``client_request_id`` collisions from the client and there is
        no phrase that produces one on purpose.
        """
        from services.undx_agent_contracts import AgentError

        self.say(client_request_id="tap_1")
        with self.assertRaises(AgentError) as caught:
            self.runs.enqueue(
                self.fx.cur, user_id=OWNER_ID, capability_id="account.health.summary",
                arguments={}, client_request_id="tap_1",
            )
        self.assertEqual(caught.exception.code, "request_id_reused")
        self.assertEqual(len(self.rows()), 1)

    def test_two_accounts_may_use_the_same_request_id(self):
        """Idempotency is scoped to a person, because request ids are minted per device.

        Were the key global, one account's ordinary tap id could deny another account the
        ability to queue anything at all — a denial of service reachable by guessing
        strings.
        """
        mine = self.say(client_request_id="tap_1")
        theirs = self.runtime.handle(self.fx.cur, user_id=OTHER_ID, text=QUEUEABLE_TEXT,
                                     client_request_id="tap_1")
        self.fx.commit()
        self.assertEqual(theirs.status, "accepted_queued")
        self.assertNotEqual(theirs.card["run_id"], mine.card["run_id"])
        self.assertEqual(len(self.rows(OWNER_ID)), 1)
        self.assertEqual(len(self.rows(OTHER_ID)), 1)

    def test_a_lookup_by_request_never_crosses_accounts(self):
        """The dedupe read is owner-scoped in the statement, not filtered afterwards."""
        self.say(client_request_id="tap_1")
        self.assertIsNotNone(self.runs.find_by_request(self.fx.cur, OWNER_ID, "tap_1"))
        self.assertIsNone(self.runs.find_by_request(self.fx.cur, OTHER_ID, "tap_1"))


class QueueingNeverBreaksTheTurn(QueueEdge):
    """A fault in deferral costs latency, never the answer."""

    def test_when_the_queue_refuses_the_action_still_runs_in_the_request(self):
        """The direction failure has to fall in.

        Surfacing the queue's error to the person would turn an infrastructure fault into
        a refusal of work the system is perfectly able to do — and would do so for a
        capability that, being on the eligible list, is one somebody deliberately made
        important enough to defer.
        """
        from unittest import mock

        from services import undx_agent_runs

        with mock.patch.object(undx_agent_runs, "enqueue",
                               side_effect=RuntimeError("queue is down")):
            response = self.say(client_request_id="tap_1")
        self.assertTrue(response.handled)
        self.assertNotEqual(response.status, "accepted_queued")
        self.assertEqual(self.rows(), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
