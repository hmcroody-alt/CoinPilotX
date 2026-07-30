"""The one sentence the fall-through rests on, tested instead of trusted.

``pulse_ai_service`` wraps the whole agent turn in ``except Exception`` and, on any
exception, hands the message to the language model as though the agent had never been
consulted. Its docstring says that is safe, and names precisely why:

    ``undx_tool_gateway.execute`` does not raise once an executor has been entered.

If that is true, an exception arriving at the caller provably precedes any mutation,
and falling through to conversation cannot paper over a real change to a user's data.
If it is false, the ``except`` *is* the bug: the model answers a question about an
action it has no idea occurred, which is the silent-degradation class this whole
programme has been closing, arriving by the one road the design assumes is shut.

The sentence had never been tested. It was false. Injecting a fault into ``_receipt``
made ``execute`` propagate it with the alert already paused in the database — because
the handler that exists to catch exactly this built its last-resort receipt by calling
``_receipt``, the same function that had just failed. A handler that re-enters the
failing call is not a handler.

So these tests are shaped as fault injection at every seam *after* the executor runs,
and each one asserts the same two things: the gateway returned rather than raised, and
what it returned does not claim more than it knows. The row is read back through the
service layer, never off the receipt — a receipt agreeing with itself proves nothing.

Two deliberate non-tests, named so the gaps are visible rather than implied:

*Pre-executor seams are not covered here.* A fault before the mutation *should*
propagate; falling through to conversation is then correct, because nothing happened.
That road is real and worth improving — the person gets a chatbot answer instead of
"something went wrong" — but it is a quality problem, not a truthfulness one.

*A fault inside ``AgentReceipt`` construction itself is not defended.* It can be
reached by patching the constructor, which is my instrument rather than a fault the
system can produce: ``_last_resort_receipt`` reads plain attributes off a frozen spec
and passes module constants for every validated field. Rather than add a third guard
that only defends against the probe, ``test_every_capability_can_build_a_last_resort_receipt``
proves the constructor cannot reject any of the 80 specs it might be handed.
"""

from __future__ import annotations

import os
import sqlite3
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from tests.undx_agent import bootstrap

bootstrap.install()

from tests.undx_agent.harness import AgentFixture, OWNER_ID  # noqa: E402

#: The capability every seam is exercised through. A write, so there is something real
#: to be wrong about, and one whose effect can be read back in a single column.
CAPABILITY = "crypto.alerts.pause"


class _Fault:
    """A callable that passes the first ``allow`` calls through, then raises.

    Needed because two of the seams are called on both sides of the executor. The
    first version of this probe patched ``_checkpoint`` outright and reported the seam
    as safe — it had stopped the turn before the executor ran, so there was no mutation
    to be wrong about and nothing had been tested. Counting calls is what makes the
    difference between "the guard held" and "the guard was never reached".

    :data:`MESSAGE` used to read ``injected fault``, which was a mistake of the same
    family. A mutation that piped the exception text straight into the user-facing
    explanation was caught by nothing, because ``injected fault`` is not a string any
    leak test would object to. The wording now is what SQLite actually says when a
    migration has not run, so a test asserting the database is not read aloud to the
    person is asserting something that could fail.
    """

    #: Deliberately a real sqlite message, naming a real column in this schema.
    MESSAGE = "no such column: pulse_follows.deleted_at"

    def __init__(self, original, allow: int = 0) -> None:
        self.original = original
        self.allow = allow
        self.seen = 0

    def __call__(self, *args, **kwargs):
        self.seen += 1
        if self.seen > self.allow:
            raise sqlite3.OperationalError(self.MESSAGE)
        return self.original(*args, **kwargs)


class PointOfNoReturnTests(unittest.TestCase):
    """Every post-executor seam, one at a time, against a real row."""

    def setUp(self) -> None:
        self.fx = AgentFixture().start()
        from services import (undx_agent_contracts, undx_architecture,
                              undx_capability_registry, undx_tool_gateway,
                              undx_verification)

        self.contracts = undx_agent_contracts
        self.architecture = undx_architecture
        self.registry = undx_capability_registry
        self.gateway = undx_tool_gateway
        self.verification = undx_verification
        self.alert_id = self.fx.make_alert(OWNER_ID, symbol="BTC", threshold=90000.0)
        self.fx.commit()

    def tearDown(self) -> None:
        self.fx.stop()

    # -- helpers ----------------------------------------------------------

    def execute(self):
        return self.gateway.execute(
            self.fx.cur, user_id=OWNER_ID, capability_id=CAPABILITY,
            proposed_arguments={"alert_id": self.alert_id},
            request_id="pnr", task_id="pnr", explicit_request=True,
            resolved_resource_count=1, question="pause it")

    def with_fault(self, module, name: str, *, allow: int = 0):
        """Break one seam for the duration of one call, then put it back."""
        original = getattr(module, name)
        fault = _Fault(original, allow=allow)
        setattr(module, name, fault)
        try:
            outcome, raised = None, None
            try:
                outcome = self.execute()
            except Exception as exc:  # noqa: BLE001 - the thing under test
                raised = exc
            self.fx.commit()
            self.assertGreater(fault.seen, fault.allow,
                               f"the fault at {name} was never reached, so this test "
                               f"proved nothing about it")
            return outcome, raised
        finally:
            setattr(module, name, original)

    def assertSettledLocally(self, outcome) -> None:
        """The fault was absorbed where it happened, not by the outer net.

        Written after two mutation modes caught nothing. Removing the inner guard on
        the reconciliation flag, and the one on the post-executor checkpoint, left
        every test passing — because the wrapper around ``_settle`` caught the escaping
        exception and returned a last-resort receipt. "The turn survived" was true
        either way, so that was all those tests were measuring.

        The two outcomes are not equivalent and the person can tell. Absorbed locally,
        the receipt still carries its verification verdict and audit state. Absorbed by
        the outer net, all of that is gone and the reply degrades to "look at the
        screen". ``settle_error`` is only ever set by the outer handler, so its absence
        is the evidence that the local guard did the work.
        """
        evidence = outcome.receipt.evidence or {}
        self.assertNotIn("settle_error", evidence,
                         "the outer wrapper caught this, so the guard under test was "
                         "never what kept the turn alive")
        self.assertIn("verification", evidence)

    def assertReturnedNotRaised(self, outcome, raised) -> None:
        if raised is not None:
            self.fail(f"execute raised {type(raised).__name__} after the executor ran; "
                      f"the alert is now "
                      f"{self.fx.alert_status(self.alert_id, OWNER_ID)!r} and the caller "
                      f"would hand this turn to the language model")
        self.assertIsNotNone(outcome)
        self.assertIsNotNone(outcome.receipt)

    # -- the seams --------------------------------------------------------

    def test_a_verification_fault_still_produces_a_receipt(self) -> None:
        """Read-back is how success is established, so losing it cannot mean success."""
        outcome, raised = self.with_fault(self.verification, "verify")
        self.assertReturnedNotRaised(outcome, raised)
        self.assertEqual("paused", self.fx.alert_status(self.alert_id, OWNER_ID))
        self.assertNotEqual(self.contracts.AgentOutcome.VERIFIED_SUCCESS,
                            outcome.receipt.status)

    def test_a_status_fault_still_produces_a_receipt(self) -> None:
        outcome, raised = self.with_fault(self.gateway, "_status_for")
        self.assertReturnedNotRaised(outcome, raised)
        self.assertNotEqual(self.contracts.AgentOutcome.VERIFIED_SUCCESS,
                            outcome.receipt.status)

    def test_an_audit_fault_is_recorded_rather_than_raised(self) -> None:
        """The mutation is real and unrecorded. Retrying would be the wrong repair."""
        outcome, raised = self.with_fault(self.architecture, "record_tool_result")
        self.assertReturnedNotRaised(outcome, raised)
        self.assertEqual("paused", self.fx.alert_status(self.alert_id, OWNER_ID))
        audit = (outcome.receipt.evidence or {}).get("audit") or {}
        self.assertTrue(audit.get("needs_reconciliation"),
                        "an unrecorded mutation has to be findable afterwards")

    def test_a_second_fault_inside_the_first_faults_handler_is_survived(self) -> None:
        """The database refusing two writes in a row must not destroy the description.

        The reconciliation flag is written by the handler for the audit failure. If it
        raises, the only remaining account of a mutation that really happened is the
        receipt, and it has to survive.
        """
        original = self.architecture.record_tool_result
        self.architecture.record_tool_result = _Fault(original)
        try:
            outcome, raised = self.with_fault(
                self.architecture, "flag_operation_for_reconciliation")
        finally:
            self.architecture.record_tool_result = original
        self.assertReturnedNotRaised(outcome, raised)
        self.assertEqual("paused", self.fx.alert_status(self.alert_id, OWNER_ID))
        self.assertSettledLocally(outcome)
        audit = (outcome.receipt.evidence or {}).get("audit") or {}
        self.assertTrue(audit.get("needs_reconciliation"),
                        "two failed writes must still leave the mutation findable")

    def test_a_post_executor_checkpoint_fault_still_produces_a_receipt(self) -> None:
        """``allow=1`` lets the pre-executor checkpoint through, which is the point.

        Without the count this test passes for the wrong reason: the turn dies before
        the executor, no row changes, and nothing about the point of no return has been
        exercised at all.

        Losing durability is not losing the verdict. The read-back still ran and the
        audit row was still written, so this turn keeps its full receipt — what it lost
        is the guarantee that the row survives a crash in the next second.
        """
        outcome, raised = self.with_fault(self.gateway, "_checkpoint", allow=1)
        self.assertReturnedNotRaised(outcome, raised)
        self.assertEqual("paused", self.fx.alert_status(self.alert_id, OWNER_ID))
        self.assertSettledLocally(outcome)
        self.assertEqual(self.contracts.AgentOutcome.VERIFIED_SUCCESS,
                         outcome.receipt.status)

    def test_a_pre_executor_checkpoint_fault_leaves_the_row_alone(self) -> None:
        """The symmetric case, and the reason raising is not always wrong.

        A fault before the executor *should* propagate. Falling through to conversation
        is then honest, because nothing happened — which is the whole basis on which the
        caller is allowed to swallow anything at all.
        """
        outcome, raised = self.with_fault(self.gateway, "_checkpoint", allow=0)
        self.assertIsNone(outcome)
        self.assertIsNotNone(raised)
        self.assertEqual("active", self.fx.alert_status(self.alert_id, OWNER_ID))

    def test_a_response_composition_fault_still_produces_a_receipt(self) -> None:
        """Wording is presentation. A defect in it is a worse sentence, not a lost turn."""
        outcome, raised = self.with_fault(self.gateway, "_compose_response")
        self.assertReturnedNotRaised(outcome, raised)
        self.assertEqual("paused", self.fx.alert_status(self.alert_id, OWNER_ID))

    def test_a_receipt_fault_still_produces_a_receipt(self) -> None:
        """The defect this batch exists for, stated as the row it must not orphan.

        ``execute``'s handler used to call ``_receipt`` to build its last-resort
        receipt. Breaking ``_receipt`` therefore broke the handler as well, and the
        exception left the gateway with the alert already paused. The fix is a receipt
        builder that touches nothing which might be the thing that failed.
        """
        outcome, raised = self.with_fault(self.gateway, "_receipt")
        self.assertReturnedNotRaised(outcome, raised)
        self.assertEqual("paused", self.fx.alert_status(self.alert_id, OWNER_ID))
        self.assertEqual(self.contracts.AgentOutcome.ACCEPTED_UNVERIFIED,
                         outcome.receipt.status)
        self.assertEqual(CAPABILITY, outcome.receipt.capability_id)

    def test_the_last_resort_receipt_does_not_claim_the_change_is_done(self) -> None:
        """A thin receipt is allowed to be thin. It is not allowed to be confident."""
        outcome, _ = self.with_fault(self.gateway, "_receipt")
        self.assertFalse(outcome.receipt.may_claim_completed)
        self.assertEqual(self.contracts.VerificationState.IMPOSSIBLE,
                         outcome.receipt.verification_state)
        self.assertTrue((outcome.receipt.evidence or {}).get("needs_reconciliation"))
        self.assertEqual("", outcome.receipt.undo_capability_id,
                         "an undo built from arguments nobody verified is a trap")

    def test_a_logging_fault_does_not_swallow_the_receipt(self) -> None:
        """The one step in the handler that talks outside the process goes first.

        It is also the least important thing happening, so it must not be the reason
        the receipt never gets built.
        """
        original = self.gateway._receipt
        self.gateway._receipt = _Fault(original)
        try:
            outcome, raised = self.with_fault(self.gateway.logger, "critical")
        finally:
            self.gateway._receipt = original
        self.assertReturnedNotRaised(outcome, raised)
        self.assertEqual("paused", self.fx.alert_status(self.alert_id, OWNER_ID))


class MatchedTurnFaultTests(unittest.TestCase):
    """The other half of the road: a fault *before* the executor.

    Raising there is truthful — nothing happened, so the caller swallowing it and
    falling through to conversation cannot mislead anyone about their data. That is why
    the tests above stop where they do. But truthful and useful are different things,
    and all seven pre-executor seams were observed producing the same nothing: the
    person asked PulseSoc to pause an alert, an index was missing, and a language model
    answered something adjacent with no sign the request had been understood.

    The rule these tests encode is a boundary, not a blanket guard. Once ``spec``
    exists the message has been recognised as a request PulseSoc knows how to serve, and
    from that point a fault owes an answer. Before it, falling through is still correct
    — a fault while deciding whether "how are you" is an action must not turn a greeting
    into an error card. So the last two tests here are as important as the first six:
    they assert the guard does *not* fire, and a guard with no boundary would pass the
    first six while quietly converting every failed greeting into a failure report.
    """

    #: The seams between "this is an action" and "the executor runs". Named as pairs so
    #: a future seam added to the runtime is a one-line addition here rather than a gap.
    #:
    #: These are attribute names and so they are also a promise about which function the
    #: turn actually calls. That promise has been broken once already: when the preview
    #: read was widened to also produce the card's resource label, the turn briefly
    #: called a differently-named function and this list went stale. The fault was still
    #: injected, the seam simply was no longer on the path, and the subtest reached a
    #: real success instead of the failure it was asserting. It failed loudly, which is
    #: the property to preserve — the assertion is on the outcome *of a fault*, so a
    #: seam that has drifted off the path shows up as an unfaulted success rather than
    #: as a quietly narrower test.
    SEAMS = (("read permission", "_read_permitted"),
             ("reference resolver", "resolve_alert_reference"),
             ("argument resolution", "resolve_arguments"),
             ("preview", "preview"))

    def setUp(self) -> None:
        self.fx = AgentFixture().start()
        from services import (undx_agent_contracts, undx_agent_runtime,
                              undx_tool_gateway)

        self.contracts = undx_agent_contracts
        self.runtime = undx_agent_runtime
        self.gateway = undx_tool_gateway
        self.alert_id = self.fx.make_alert(OWNER_ID, symbol="BTC", threshold=90000.0)
        self.fx.commit()

    def tearDown(self) -> None:
        self.fx.stop()

    def say(self, text: str, module=None, name: str = "", allow: int = 0):
        """One turn, with one seam optionally broken for its duration."""
        module = module or self.runtime
        original = getattr(module, name) if name else None
        if name:
            setattr(module, name, _Fault(original, allow=allow))
        try:
            try:
                return self.runtime.handle(self.fx.cur, user_id=OWNER_ID, text=text,
                                           correlation_id="b10"), None
            except Exception as exc:  # noqa: BLE001 - the thing under test
                return None, exc
        finally:
            if name:
                setattr(module, name, original)

    def test_every_pre_executor_seam_answers_instead_of_vanishing(self) -> None:
        for label, name in self.SEAMS:
            with self.subTest(seam=label):
                turn, raised = self.say("pause my bitcoin alert", name=name)
                self.assertIsNone(raised, f"{label} still escapes the turn")
                self.assertTrue(turn.handled)
                self.assertEqual(self.contracts.AgentOutcome.RECOVERABLE_FAILURE,
                                 turn.receipt.status)
                self.assertEqual("active",
                                 self.fx.alert_status(self.alert_id, OWNER_ID))

    def test_a_gateway_fault_before_the_executor_answers_too(self) -> None:
        """The seam that reaches furthest in without the executor having run."""
        turn, raised = self.say("pause my bitcoin alert", module=self.gateway,
                                name="_enforce_permission_scope")
        self.assertIsNone(raised)
        self.assertEqual(self.contracts.AgentOutcome.RECOVERABLE_FAILURE,
                         turn.receipt.status)
        self.assertEqual("active", self.fx.alert_status(self.alert_id, OWNER_ID))

    def test_the_answer_is_a_retry_not_a_dead_end(self) -> None:
        """Nothing happened, so trying again is safe — and the card has to say so.

        ``RECOVERABLE_FAILURE`` renders as a retry card, which is why this needed no new
        wire enum. Reporting it as ``TERMINAL_FAILURE`` would be the pessimistic lie
        that matches the optimistic one this programme has spent nine batches removing.
        """
        turn, _ = self.say("pause my bitcoin alert", name="resolve_arguments")
        self.assertEqual(self.contracts.CardType.RETRY_ACTION,
                         (turn.card or {}).get("component"))
        self.assertFalse((turn.card or {}).get("verified"))
        self.assertFalse(turn.receipt.may_claim_completed)
        self.assertIs(False, turn.receipt.evidence.get("reached_executor"))

    def test_the_fault_names_the_capability_it_could_not_run(self) -> None:
        """The whole difference from falling through. The person sees it was understood."""
        turn, _ = self.say("pause my bitcoin alert", name="resolve_arguments")
        self.assertEqual("crypto.alerts.pause", turn.receipt.capability_id)
        self.assertEqual("crypto.alerts.pause", (turn.card or {}).get("capability_id"))

    def test_the_database_is_not_quoted_back_at_the_person(self) -> None:
        """Exception text carries schema fragments and other rows' identifiers.

        A fault is the worst possible moment to start reading the database aloud, so
        the type is recorded in evidence and the message is not forwarded anywhere the
        person can see.
        """
        turn, _ = self.say("pause my bitcoin alert", name="resolve_arguments")
        visible = " ".join([turn.reply or "", turn.receipt.user_explanation,
                            str((turn.card or {}).get("message") or "")])
        self.assertNotIn("deleted_at", visible)
        self.assertNotIn("no such column", visible)
        self.assertEqual("OperationalError", turn.receipt.evidence.get("fault"))

    def test_a_fault_before_the_message_is_understood_still_falls_through(self) -> None:
        """The boundary, from the side that must not fire.

        ``match_capability`` runs before anything is known about the message. A guard
        that covered it too would convert every unrecognised sentence, during any
        hiccup, into "PulseSoc could not do that" — an error report for a request nobody
        made. Falling through is right here, and it is right for the same reason it was
        always right: nothing happened, and nothing was even asked for.
        """
        turn, raised = self.say("pause my bitcoin alert", name="match_capability")
        self.assertIsNone(turn)
        self.assertIsNotNone(raised)

    def test_ordinary_conversation_is_untouched_by_the_guard(self) -> None:
        """A greeting is not an action, so a broken resolver must not make it one."""
        turn, raised = self.say("how are you today", name="resolve_arguments")
        self.assertIsNone(raised)
        self.assertFalse(turn.handled)
        self.assertIsNone(turn.receipt)

    def test_a_typed_refusal_keeps_its_own_outcome(self) -> None:
        """An ``AgentError`` escaping the action is a refusal, not an infrastructure fault.

        It already carries a canonical outcome. Flattening it into
        ``RECOVERABLE_FAILURE`` would invite the person to retry something that will
        refuse them identically every time.
        """
        from services.undx_agent_contracts import AgentError, AgentOutcome

        def refuse(*args, **kwargs):
            raise AgentError("permission_denied", "UNDX cannot do that for you.",
                             outcome=AgentOutcome.PERMISSION_DENIED)

        original = self.runtime.resolve_arguments
        self.runtime.resolve_arguments = refuse
        try:
            turn = self.runtime.handle(self.fx.cur, user_id=OWNER_ID,
                                       text="pause my bitcoin alert",
                                       correlation_id="b10")
        finally:
            self.runtime.resolve_arguments = original
        self.assertTrue(turn.handled)
        self.assertEqual(AgentOutcome.PERMISSION_DENIED, turn.receipt.status)

    def test_a_logging_fault_does_not_swallow_the_answer(self) -> None:
        """The batch's own fix must not reintroduce the batch's own bug.

        ``_fault_response`` logs before it builds anything, and logging is the only step
        in it that talks to something outside the process — a handler writing to a full
        disk, a socket to a collector that has gone away. Unguarded, that becomes the
        reason the receipt is never built, and the person is back to the silence this
        batch exists to remove, now by a longer route.

        Written because a mutation removing that guard was caught by nothing: every
        other test here uses a logger that works. A defence no test exercises is a
        comment.
        """
        class _BrokenErrorLog:
            def __init__(self, real) -> None:
                self.real = real

            def __getattr__(self, name):
                return getattr(self.real, name)

            def error(self, *args, **kwargs):
                raise OSError("log handler is gone")

        original_logger = self.runtime.logger
        self.runtime.logger = _BrokenErrorLog(original_logger)
        try:
            turn, raised = self.say("pause my bitcoin alert", name="resolve_arguments")
        finally:
            self.runtime.logger = original_logger
        self.assertIsNone(raised, "a broken log handler ate the turn")
        self.assertTrue(turn.handled)
        self.assertEqual(self.contracts.AgentOutcome.RECOVERABLE_FAILURE,
                         turn.receipt.status)


class LastResortReceiptTests(unittest.TestCase):
    """The fallback builder on its own, across everything it might be handed.

    This is what stands in for a third layer of guard. ``AgentReceipt.__post_init__``
    rejects an unknown outcome or verification state, so the one realistic way the
    fallback could fail is a future edit that passes it something invalid — for one
    capability, or for a risk level only a few specs use. Building a receipt for every
    spec in the registry catches that at test time rather than inside the handler.
    """

    def setUp(self) -> None:
        from services import (undx_agent_contracts, undx_capability_registry,
                              undx_tool_gateway)

        self.contracts = undx_agent_contracts
        self.registry = undx_capability_registry
        self.gateway = undx_tool_gateway

    def test_every_capability_can_build_a_last_resort_receipt(self) -> None:
        outcomes = (self.contracts.AgentOutcome.ACCEPTED_UNVERIFIED,
                    self.contracts.AgentOutcome.TERMINAL_FAILURE)
        built = 0
        for capability_id, spec in self.registry.REGISTRY.items():
            for status in outcomes:
                receipt = self.gateway._last_resort_receipt(
                    spec, user_id=OWNER_ID, request_id="r", task_id="t",
                    status=status, explanation="something went wrong",
                    evidence={"settle_error": "OperationalError"})
                self.assertEqual(capability_id, receipt.capability_id)
                self.assertEqual(status, receipt.status)
                self.assertFalse(receipt.may_claim_completed)
                built += 1
        self.assertEqual(len(self.registry.REGISTRY) * len(outcomes), built)

    def test_it_does_not_call_anything_that_could_be_the_failure(self) -> None:
        """The property that makes it a fallback rather than a second thing to break.

        Asserted against the spec's own methods rather than by reading the source:
        every one of them is replaced with a raiser, and the receipt still gets built.
        ``deep_link``, ``undo_arguments`` and ``clean`` are exactly what ``_receipt``
        reaches for, and any of them may be why the handler was entered.
        """
        spec = self.registry.REGISTRY[CAPABILITY]

        def boom(*args, **kwargs):
            raise sqlite3.OperationalError("injected fault")

        saved = {name: getattr(type(spec), name, None)
                 for name in ("deep_link", "undo_arguments")}
        saved_clean = self.gateway.clean
        for name in saved:
            if saved[name] is not None:
                setattr(type(spec), name, boom)
        self.gateway.clean = boom
        try:
            receipt = self.gateway._last_resort_receipt(
                spec, user_id=OWNER_ID, request_id="r", task_id="t",
                status=self.contracts.AgentOutcome.ACCEPTED_UNVERIFIED,
                explanation="something went wrong", evidence={})
        finally:
            self.gateway.clean = saved_clean
            for name, value in saved.items():
                if value is not None:
                    setattr(type(spec), name, value)
        self.assertEqual(CAPABILITY, receipt.capability_id)
        self.assertEqual("", receipt.native_deep_link)


if __name__ == "__main__":
    unittest.main()
