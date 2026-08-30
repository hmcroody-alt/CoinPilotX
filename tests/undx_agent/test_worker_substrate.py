"""What makes a worker a worker: it runs elsewhere, it claims, and a crash costs nothing.

Stages 14 through 17 are four separate claims about the same substrate, and each one has a
characteristic false green that this file exists to make unavailable.

**Stage 16 — the claim proof.** The directive states the trap in one sentence: *do not call
synchronous request execution a worker PASS.* Every capability in the eligible set also
executes perfectly well inside a Flask request, so a QA run that queues a run and then
observes the right answer coming back has proven nothing about whether a worker was
involved. The distinguishing evidence is not the answer; it is the row. A worker pass
leaves a claimed, leased, settled row in ``undx_agent_runs`` bearing a worker identity. A
synchronous turn leaves no row at all. The tests here assert the row.

**Stage 9-12 — the decoupling, proven by denial.** ``undx_worker.py`` used to open with
``import bot``, and the failure was concrete: ``ModuleNotFoundError: No module named
'stripe'``, raised inside a process that will never take a payment. Asserting that the
source no longer contains ``import bot`` is a grep, and a grep is satisfied by a transitive
import three modules down. So the proof here is a subprocess that makes ``bot``, ``stripe``
and ``flask`` *unimportable* and then imports the worker anyway. That is the property
Railway actually depends on, and it is the property Stage 34's clean-checkout test needs.

**Stage 33 support — the heartbeat cannot drift.** ``services.undx_worker_runtime`` carries
a copy of the ``worker_heartbeats`` DDL, duplicated on purpose because importing the
original is the entire problem it solves. Its own docstring promises "a drift test asserts
the column set matches what the web service writes". This is that test. Two processes
issuing ``CREATE TABLE IF NOT EXISTS`` against one database is safe only while the two
definitions agree; the day they stop agreeing, whichever process boots second silently gets
the other's table and writes into columns it did not expect.

**Stage 17 — a crash repeats nothing.** The queue's job on re-claim is to hand the gateway
the *same* request id, not to decide whether the work already happened. That decision
belongs to the gateway's idempotency ledger, which reserved a row before the executor ran.
The test therefore asserts the identity of what is passed, not the absence of a second
execution — because a queue that suppressed the second call itself would be a second
implementation of idempotency, and two implementations of one rule is how the rule breaks.

**Stage 15 — nothing about a run comes from the client.** Asserted on the envelope columns
rather than on route handlers, which ``test_run_retrieval`` and ``test_run_lifecycle``
already cover from the HTTP side. The columns that decide what executes — capability,
canonical target, idempotency key, attempt bounds — have exactly one author each, and none
of them is a caller.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import textwrap
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from tests.undx_agent import bootstrap

bootstrap.install()

from tests.undx_agent.harness import AgentFixture, OTHER_ID, OWNER_ID  # noqa: E402


ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

RUN_FLAGS = {
    "UNDX_AGENT_RUNS_ENABLED": "1",
    "UNDX_WORKER_ENABLED": "1",
    "UNDX_BRAIN_ENABLED": "1",
}

A_READ = "activity.daily_summary"

#: The three the worker must not need. ``bot`` because it is the web application and
#: importing it costs 111k lines and every route pack; ``stripe`` because it was the
#: observed failure and a worker that needs the payment SDK cannot be given a reduced
#: credential set; ``flask`` because a background process with a web framework in it is a
#: background process that can grow a request handler.
FORBIDDEN_IMPORTS = ("bot", "stripe", "flask")


class _Receipt:
    def __init__(self, status: str) -> None:
        self.status = status
        self.explanation = ""


class _Answer:
    """The two fields of a gateway answer that the queue reads.

    The second one is ``succeeded``, which is what
    :class:`~services.undx_tool_gateway.GatewayOutcome` actually carries. It was
    ``may_claim_completed`` here, a name that outcome has never had and that its
    ``__slots__`` make permanently unaddable — and because the settlement rule reached for
    it through ``getattr(..., False)``, the stub and the rule agreed with each other about
    a field neither the real gateway nor the real queue could ever exchange. Every run in
    production settled ``failed``; this suite passed throughout. The lesson is narrower
    than "avoid stubs": a stub is a claim about a contract, so the claim has to be checked
    against the contract, which
    ``tests/undx_agent/test_run_lifecycle.py::TheStubMatchesTheRealContract`` now does.
    """

    def __init__(self, status: str, succeeded: bool) -> None:
        self.receipt = _Receipt(status)
        self.succeeded = succeeded


class TheWorkerDoesNotImportTheWebApplication(unittest.TestCase):
    """Stages 9 through 12, proven by denial rather than by reading the source."""

    def _import_under_denial(self, module: str) -> subprocess.CompletedProcess:
        """Import ``module`` in a fresh interpreter where the web stack cannot be imported.

        A meta-path finder that raises is used rather than deleting entries from
        ``sys.modules``, because the question is not whether the modules are *loaded* — a
        lazy import three levels down would pass that — but whether any code path reached
        during import asks for them at all.

        Run as a subprocess because ``bot`` may already be in this interpreter's
        ``sys.modules`` from another test in the same run, in which case an in-process
        blocker would never be consulted and the test would pass for the wrong reason.
        """
        program = textwrap.dedent(
            f"""
            import sys
            sys.path.insert(0, {ROOT!r})

            class Denied:
                names = {FORBIDDEN_IMPORTS!r}

                def find_spec(self, name, path=None, target=None):
                    if name.split(".")[0] in self.names:
                        raise ImportError("DENIED:" + name)
                    return None

            sys.meta_path.insert(0, Denied())

            # Prove the denier works before trusting what it permits. Without this, a
            # broken blocker makes every assertion below vacuously true.
            for forbidden in {FORBIDDEN_IMPORTS!r}:
                try:
                    __import__(forbidden)
                except ImportError:
                    pass
                else:
                    print("BLOCKER_INEFFECTIVE:" + forbidden)
                    raise SystemExit(2)

            import {module}
            print("IMPORTED")
            for forbidden in {FORBIDDEN_IMPORTS!r}:
                if forbidden in sys.modules:
                    print("LEAKED:" + forbidden)
                    raise SystemExit(3)
            raise SystemExit(0)
            """
        )
        return subprocess.run(
            [sys.executable, "-c", program],
            capture_output=True, text=True, timeout=120, cwd=ROOT,
        )

    def test_the_worker_entrypoint_imports_with_the_web_stack_unavailable(self) -> None:
        """The whole Stage 9-12 claim, in one assertion.

        This is the shape of the original bug: ``import undx_worker`` raising
        ``ModuleNotFoundError: No module named 'stripe'`` from ``bot.py`` line 22.
        """
        result = self._import_under_denial("undx_worker")
        self.assertEqual(
            result.returncode, 0,
            f"the worker still needs the web application to import\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr[-2000:]}")
        self.assertIn("IMPORTED", result.stdout)

    def test_every_module_the_worker_polls_imports_the_same_way(self) -> None:
        """Swept over the worker's own dependency list rather than the entrypoint alone.

        ``undx_worker`` importing cleanly today is one arrangement of imports. These four
        are the modules it calls on every pass, and any one of them acquiring a web-stack
        dependency would break the worker at runtime rather than at import — which is the
        worse of the two failure times, because the process would already be reporting
        healthy.
        """
        for module in ("services.undx_worker_runtime", "services.undx_agent_runs",
                       "services.undx_mission_runtime", "services.undx_tool_gateway"):
            with self.subTest(module):
                result = self._import_under_denial(module)
                self.assertEqual(
                    result.returncode, 0,
                    f"{module} pulls in the web stack\nstderr: {result.stderr[-1500:]}")

    def test_the_entrypoint_names_none_of_them_directly(self) -> None:
        """A cheap second reading, in the one form the subprocess test cannot give.

        The denial test proves nothing is *reached*. This proves nothing is *written*,
        which is what a reviewer sees first and what a future edit is most likely to
        reintroduce.
        """
        source = open(os.path.join(ROOT, "undx_worker.py"), encoding="utf-8").read()
        for forbidden in FORBIDDEN_IMPORTS:
            with self.subTest(forbidden):
                self.assertIsNone(
                    re.search(rf"^\s*(import|from)\s+{forbidden}\b", source, re.M),
                    f"undx_worker.py imports {forbidden}")

    def test_the_worker_start_marker_survives_the_decoupling(self) -> None:
        """Railway reads stdout for this string, so it is part of the contract.

        It is a ``print`` rather than a log call precisely *because* of the decoupling:
        the worker no longer inherits whatever logging configuration importing the web
        application happened to install first.
        """
        source = open(os.path.join(ROOT, "undx_worker.py"), encoding="utf-8").read()
        self.assertIn("UNDX_WORKER_START", source)
        self.assertRegex(source, r"print\(\s*\n?\s*\"UNDX_WORKER_START")


class TheHeartbeatDefinitionCannotDrift(unittest.TestCase):
    """Stage 33 support. Two processes, one table, and no way to disagree about it."""

    @staticmethod
    def _columns_from_ddl(ddl: str) -> tuple[str, ...]:
        """Column names from a ``CREATE TABLE`` body, in declaration order.

        Deliberately naive: it takes the first token of each comma-separated line inside
        the parentheses. A definition complicated enough to defeat this parser is a
        definition too complicated for a table two processes create independently.
        """
        body = ddl[ddl.index("(") + 1: ddl.rindex(")")]
        names = []
        for piece in body.split(","):
            token = piece.strip().split()
            if token and token[0].upper() not in {"PRIMARY", "UNIQUE", "FOREIGN", "CHECK"}:
                names.append(token[0])
        return tuple(names)

    def _web_definition(self) -> str:
        source = open(os.path.join(ROOT, "bot.py"), encoding="utf-8").read()
        match = re.search(
            r"CREATE TABLE IF NOT EXISTS worker_heartbeats\s*\((?:[^()]|\([^()]*\))*\)",
            source)
        self.assertIsNotNone(
            match, "bot.py no longer declares worker_heartbeats — this test is stale")
        return match.group(0)

    def test_the_worker_copy_declares_the_web_services_columns(self) -> None:
        """The promise made in ``undx_worker_runtime``'s own docstring, enforced.

        ``CREATE TABLE IF NOT EXISTS`` from two processes is safe only while the two
        statements agree. When they diverge, whichever process boots second silently
        inherits the other's table and writes into a shape it did not declare — and
        nothing fails until a column it expected turns out not to exist.
        """
        from services import undx_worker_runtime

        self.assertEqual(
            self._columns_from_ddl(undx_worker_runtime.HEARTBEAT_DDL),
            self._columns_from_ddl(self._web_definition()))

    def test_the_declared_column_tuple_matches_the_ddl_it_sits_beside(self) -> None:
        """``HEARTBEAT_COLUMNS`` is read by the health surface, so it is a third copy.

        Three copies of one fact is two too many, but the other two are load-bearing —
        one is executed as SQL, one is imported by a reader. This asserts the third
        cannot drift from the first.
        """
        from services import undx_worker_runtime

        self.assertEqual(
            tuple(undx_worker_runtime.HEARTBEAT_COLUMNS),
            self._columns_from_ddl(undx_worker_runtime.HEARTBEAT_DDL))

    def test_the_upsert_writes_every_column_it_declares(self) -> None:
        """A column present in the DDL and absent from the insert is a column that is
        always NULL — which reads, to a dashboard, exactly like a worker that never
        reported it."""
        from services import undx_worker_runtime

        source = open(undx_worker_runtime.__file__, encoding="utf-8").read()
        insert = re.search(r"INSERT INTO worker_heartbeats\s*\(([^)]*)\)", source)
        self.assertIsNotNone(insert)
        written = tuple(name.strip() for name in insert.group(1).split(","))
        self.assertEqual(written, tuple(undx_worker_runtime.HEARTBEAT_COLUMNS))

    def test_the_worker_creates_only_what_it_owns(self) -> None:
        """Least privilege, applied to schema.

        ``bot.init_db()`` builds roughly 170 tables spanning payments, media, live
        sessions and marketplace. A worker that reproduced that would be creating the
        product's schema from a process with no business owning any of it. The one table
        it creates directly is its own heartbeat; the UNDX tables are delegated to the
        modules that define them, so each has exactly one definition.
        """
        from services import undx_worker_runtime

        source = open(undx_worker_runtime.__file__, encoding="utf-8").read()
        created = set(re.findall(r"CREATE TABLE IF NOT EXISTS (\w+)", source))
        self.assertEqual(created, {"worker_heartbeats"})


class OnlyTheWorkerExecutesAQueuedRun(unittest.TestCase):
    """Stage 16. The evidence for a worker pass is the row, never the answer."""

    def setUp(self) -> None:
        self.fx = AgentFixture(**RUN_FLAGS).start()
        from services import undx_agent_runs

        self.runs = undx_agent_runs
        self.runs.ensure_schema(self.fx.cur)
        self.fx.commit()

    def tearDown(self) -> None:
        self.fx.stop()

    def _queue(self, *, user_id=OWNER_ID, request_id="req_1"):
        run_id = self.runs.enqueue(
            self.fx.cur, user_id=user_id, capability_id=A_READ, arguments={},
            confirmation_id="", client_request_id=request_id,
        )
        self.fx.commit()
        return run_id

    def _row(self, run_id: str) -> dict:
        self.fx.cur.execute("SELECT * FROM undx_agent_runs WHERE run_id=?", (run_id,))
        return dict(self.fx.cur.fetchone())

    def test_queueing_alone_executes_nothing(self) -> None:
        """The row a QA reviewer must see before believing anything else happened.

        If enqueue also executed, every downstream claim about worker involvement would
        be about a job that had already run.
        """
        from services import undx_tool_gateway

        calls = []
        original = undx_tool_gateway.execute
        undx_tool_gateway.execute = lambda *a, **k: calls.append(k) or _Answer("x", False)
        try:
            run_id = self._queue()
        finally:
            undx_tool_gateway.execute = original

        self.assertEqual(calls, [], "enqueue reached the gateway")
        row = self._row(run_id)
        self.assertEqual(row["status"], "queued")
        self.assertEqual(int(row["attempt_count"] or 0), 0)
        self.assertEqual(str(row["lease_owner"] or ""), "")

    def test_a_settled_run_carries_the_marks_of_having_been_claimed(self) -> None:
        """Stage 16's actual acceptance evidence.

        A synchronous turn produces the same user-visible answer and leaves no row at
        all, so the row itself is most of the proof. What is *on* it matters too: a spent
        attempt and a completion stamp are written only by the claim-execute-settle path.

        Note what is deliberately not asserted — a worker identity. ``_settle`` releases
        the lease as part of settling, which is correct: a terminal run holds no lease and
        a row that still named an owner would keep a dead container's name on it forever.
        The identity is load-bearing at settle time rather than after it, and the next
        test is the one that proves it.
        """
        from services import undx_agent_runs, undx_tool_gateway
        from services.undx_agent_contracts import AgentOutcome

        run_id = self._queue()
        claimed = undx_agent_runs.claim_next(self.fx.cur, "worker_alpha")
        self.fx.commit()
        self.assertIsNotNone(claimed)
        self.assertEqual(str(claimed["run_id"]), run_id)
        self.assertEqual(str(claimed["lease_owner"]), "worker_alpha")

        original = undx_tool_gateway.execute
        undx_tool_gateway.execute = (
            lambda *a, **k: _Answer(AgentOutcome.VERIFIED_SUCCESS, True))
        try:
            result = undx_agent_runs.execute_claimed(self.fx.cur, claimed, "worker_alpha")
            self.fx.commit()
        finally:
            undx_tool_gateway.execute = original

        self.assertTrue(result["executed"])
        row = self._row(run_id)
        self.assertEqual(row["status"], "succeeded")
        self.assertGreaterEqual(int(row["attempt_count"] or 0), 1)
        self.assertTrue(str(row["completed_at"] or ""))
        self.assertEqual(str(row["lease_owner"] or ""), "",
                         "a settled run holds no lease")

    def test_settling_is_scoped_to_the_process_that_held_the_lease(self) -> None:
        """The identity check that the released lease makes invisible afterwards.

        A worker whose lease lapsed mid-execution must not overwrite the state written by
        whoever reclaimed the run. Without the scope, the slow container's stale answer
        lands last and wins.
        """
        from services import undx_agent_runs
        from services.undx_agent_contracts import AgentOutcome

        run_id = self._queue()
        claimed = undx_agent_runs.claim_next(self.fx.cur, "worker_alpha")
        self.fx.commit()

        undx_agent_runs._settle(
            self.fx.cur, claimed, worker_id="worker_beta", status="succeeded",
            outcome=AgentOutcome.VERIFIED_SUCCESS)
        self.fx.commit()

        row = self._row(run_id)
        self.assertEqual(row["status"], "running",
                         "a process that never held the lease settled the run")
        self.assertEqual(str(row["lease_owner"]), "worker_alpha")

    def test_a_process_that_did_not_claim_the_run_cannot_execute_it(self) -> None:
        """The lease is authorisation, not bookkeeping.

        Without this, two containers polling the same queue both execute every run, and
        the second one's answer overwrites the first's — one approval, two executions,
        one receipt.
        """
        from services import undx_agent_runs, undx_tool_gateway

        run_id = self._queue()
        claimed = undx_agent_runs.claim_next(self.fx.cur, "worker_alpha")
        self.fx.commit()

        calls = []
        original = undx_tool_gateway.execute
        undx_tool_gateway.execute = lambda *a, **k: calls.append(k) or _Answer("x", False)
        try:
            result = undx_agent_runs.execute_claimed(self.fx.cur, claimed, "worker_beta")
            self.fx.commit()
        finally:
            undx_tool_gateway.execute = original

        self.assertFalse(result["executed"])
        self.assertEqual(result["reason"], "lease_not_owned")
        self.assertEqual(calls, [], "an unleased process reached the gateway")
        self.assertEqual(self._row(run_id)["status"], "running")

    def test_the_deployed_loop_is_the_thing_that_calls_the_claim_path(self) -> None:
        """A claim path nothing calls is a claim path that never runs in production.

        ``poll_once`` being correct and ``poll_once`` being wired into the process Railway
        starts are different facts, and only the second one puts a run through a worker.
        """
        source = open(os.path.join(ROOT, "undx_worker.py"), encoding="utf-8").read()
        self.assertIn("undx_agent_runs.poll_once()", source)
        loop = source[source.index("while not STOP_EVENT.is_set():"):]
        self.assertIn("undx_agent_runs.poll_once()", loop,
                      "the run poll is not inside the worker's loop")

    def test_the_run_poll_is_isolated_from_the_mission_poll(self) -> None:
        """Two independent kinds of work, so a failure in one is not evidence about the
        other. Folded together, a mission-storage error silently stops the execution of
        actions somebody already approved."""
        source = open(os.path.join(ROOT, "undx_worker.py"), encoding="utf-8").read()
        loop = source[source.index("while not STOP_EVENT.is_set():"):]
        poll = loop.index("undx_agent_runs.poll_once()")
        # The run poll sits inside its own try, whose except sets a reason rather than
        # letting the pass abort.
        self.assertIn("try:", loop[:poll])
        self.assertIn("UNDX_WORKER_RUN_POLL_FAILED", loop)

    def test_poll_once_claims_and_settles_a_real_row_end_to_end(self) -> None:
        """The full worker half over a real database, not the two halves separately.

        ``poll_once`` opens its own connection, so this also exercises the property that
        makes the worker independent: it reaches the queue through ``services.db`` and
        needs nothing that a request would have set up for it.
        """
        from services import undx_agent_runs, undx_tool_gateway
        from services.undx_agent_contracts import AgentOutcome

        run_id = self._queue(request_id="req_poll")

        original = undx_tool_gateway.execute
        undx_tool_gateway.execute = (
            lambda *a, **k: _Answer(AgentOutcome.VERIFIED_SUCCESS, True))
        try:
            outcome = undx_agent_runs.poll_once()
        finally:
            undx_tool_gateway.execute = original

        self.assertTrue(outcome["enabled"])
        self.assertTrue(outcome["executed"])
        self.assertEqual(outcome["run_id"], run_id)

        row = self._row(run_id)
        self.assertEqual(row["status"], "succeeded")
        self.assertGreaterEqual(int(row["attempt_count"] or 0), 1,
                                "a settled run must have spent a claim attempt")
        self.assertTrue(str(row["completed_at"] or ""))

    def test_a_second_pass_finds_nothing_left_to_claim(self) -> None:
        """One run per pass, and a settled run does not return to the queue.

        The failure this excludes is the expensive one: a settled row that stays
        claimable re-executes on every subsequent pass, forever, against an approval that
        was granted once.
        """
        from services import undx_agent_runs, undx_tool_gateway
        from services.undx_agent_contracts import AgentOutcome

        self._queue(request_id="req_once")
        original = undx_tool_gateway.execute
        undx_tool_gateway.execute = (
            lambda *a, **k: _Answer(AgentOutcome.VERIFIED_SUCCESS, True))
        try:
            first = undx_agent_runs.poll_once()
            second = undx_agent_runs.poll_once()
        finally:
            undx_tool_gateway.execute = original

        self.assertTrue(first["executed"])
        self.assertFalse(second["executed"])
        self.assertEqual(second["reason"], "no_eligible_run")

    def test_a_worker_never_reaches_another_accounts_run(self) -> None:
        """Stage 14 from the worker's side.

        The read APIs are owner-scoped in their statements, but a worker has no session
        and no owner — it claims whatever is claimable. What protects the account is that
        the owner travels *on the row* and is handed to the gateway from there, so a
        worker cannot substitute one.
        """
        from services import undx_agent_runs, undx_tool_gateway

        self.runs.enqueue(
            self.fx.cur, user_id=OTHER_ID, capability_id=A_READ, arguments={},
            confirmation_id="", client_request_id="req_other",
        )
        self.fx.commit()

        seen = []
        original = undx_tool_gateway.execute

        def record(cur, **kwargs):
            seen.append(kwargs.get("user_id"))
            return _Answer("x", False)

        undx_tool_gateway.execute = record
        try:
            undx_agent_runs.poll_once()
        finally:
            undx_tool_gateway.execute = original

        self.assertEqual(seen, [OTHER_ID],
                         "the gateway must be given the owner from the row and no other")


class ACrashBetweenClaimAndSettleRepeatsNothing(unittest.TestCase):
    """Stage 17. Resumable, which is a different property from retriable."""

    def setUp(self) -> None:
        self.fx = AgentFixture(**RUN_FLAGS).start()
        from services import undx_agent_runs

        self.runs = undx_agent_runs
        self.runs.ensure_schema(self.fx.cur)
        self.fx.commit()

    def tearDown(self) -> None:
        self.fx.stop()

    def _queue(self, request_id="req_1"):
        run_id = self.runs.enqueue(
            self.fx.cur, user_id=OWNER_ID, capability_id=A_READ, arguments={},
            confirmation_id="", client_request_id=request_id,
        )
        self.fx.commit()
        return run_id

    def _row(self, run_id: str) -> dict:
        self.fx.cur.execute("SELECT * FROM undx_agent_runs WHERE run_id=?", (run_id,))
        return dict(self.fx.cur.fetchone())

    def test_an_untyped_crash_returns_the_run_to_the_queue_without_its_lease(self) -> None:
        """An unexpected exception is not known to be safe to repeat, and is not known to
        be unsafe either. Releasing the lease lets the gateway's ledger — which reserved a
        row before the executor ran — be the thing that decides."""
        from services import undx_agent_runs, undx_tool_gateway

        run_id = self._queue()
        claimed = undx_agent_runs.claim_next(self.fx.cur, "worker_alpha")
        self.fx.commit()

        def boom(*a, **k):
            raise RuntimeError("container died")

        original = undx_tool_gateway.execute
        undx_tool_gateway.execute = boom
        try:
            result = undx_agent_runs.execute_claimed(self.fx.cur, claimed, "worker_alpha")
            self.fx.commit()
        finally:
            undx_tool_gateway.execute = original

        self.assertFalse(result["executed"])
        row = self._row(run_id)
        self.assertEqual(row["status"], "queued")
        self.assertEqual(str(row["lease_owner"] or ""), "")
        self.assertEqual(row["last_error"], "RuntimeError",
                         "the class, never the message — a message can carry an argument")

    def test_the_gateway_sees_the_same_request_id_on_every_attempt(self) -> None:
        """The whole of Stage 17, and the reason it is asserted on identity rather than on
        a call count.

        The queue does not suppress the second call. It hands the gateway the same anchor
        both times and lets the ledger row reserved by the first attempt refuse the
        second. A queue that decided this itself would be a second implementation of
        idempotency, and two implementations of one rule disagree the first time either
        moves.
        """
        from services import undx_agent_runs, undx_tool_gateway

        run_id = self._queue()
        anchors = []

        def record(cur, **kwargs):
            anchors.append((kwargs.get("request_id"), kwargs.get("client_request_id"),
                            kwargs.get("task_id")))
            raise RuntimeError("crash after reservation")

        original = undx_tool_gateway.execute
        undx_tool_gateway.execute = record
        try:
            for worker in ("worker_alpha", "worker_beta"):
                claimed = undx_agent_runs.claim_next(self.fx.cur, worker)
                self.fx.commit()
                self.assertIsNotNone(claimed, "the crashed run did not return to the queue")
                undx_agent_runs.execute_claimed(self.fx.cur, claimed, worker)
                self.fx.commit()
        finally:
            undx_tool_gateway.execute = original

        self.assertEqual(len(anchors), 2)
        self.assertEqual(anchors[0], anchors[1],
                         "a re-claim changed the idempotency anchor, making the retry a "
                         "second distinct request rather than a resumption")
        self.assertEqual(anchors[0][0], run_id)

    def test_the_anchor_is_the_run_id_and_not_the_attempt(self) -> None:
        """Stated separately because the tempting bug is to make it unique per try.

        A request id that included the attempt number would be a perfectly reasonable
        thing to write and would silently convert every recovery into a duplicate
        execution.
        """
        from services import undx_agent_runs, undx_tool_gateway

        run_id = self._queue()
        claimed = undx_agent_runs.claim_next(self.fx.cur, "worker_alpha")
        self.fx.commit()

        seen = {}
        original = undx_tool_gateway.execute

        def record(cur, **kwargs):
            seen.update(kwargs)
            return _Answer("x", False)

        undx_tool_gateway.execute = record
        try:
            undx_agent_runs.execute_claimed(self.fx.cur, claimed, "worker_alpha")
            self.fx.commit()
        finally:
            undx_tool_gateway.execute = original

        self.assertEqual(seen["request_id"], run_id)
        self.assertEqual(seen["task_id"], run_id)
        self.assertNotIn(str(int(claimed["attempt_count"] or 0) + 1),
                         str(seen["request_id"]).replace(run_id, ""))

    def test_the_worker_supplies_no_authority_of_its_own(self) -> None:
        """Every field the gateway authorises against comes off the row.

        The worker has no session, so anything it invented here would be authority
        created by a background process on behalf of an absent person.
        """
        from services import undx_agent_runs, undx_tool_gateway

        self._queue()
        claimed = undx_agent_runs.claim_next(self.fx.cur, "worker_alpha")
        self.fx.commit()

        seen = {}
        original = undx_tool_gateway.execute

        def record(cur, **kwargs):
            seen.update(kwargs)
            return _Answer("x", False)

        undx_tool_gateway.execute = record
        try:
            undx_agent_runs.execute_claimed(self.fx.cur, claimed, "worker_alpha")
            self.fx.commit()
        finally:
            undx_tool_gateway.execute = original

        self.assertEqual(seen["user_id"], OWNER_ID)
        self.assertEqual(seen["capability_id"], str(claimed["capability_id"]))
        self.assertEqual(seen["confirmation_id"], str(claimed["confirmation_id"] or ""))
        self.assertFalse(
            seen["target_chosen_by_agent"],
            "a deterministic resolver chose this target in the request; saying otherwise "
            "would understate the risk, and saying it falsely would overstate it")

    def test_a_crash_loop_ends_in_a_dead_letter_rather_than_forever(self) -> None:
        """Resumable is bounded. Otherwise one poisonous run occupies every pass of every
        worker for the life of the deployment."""
        from services import undx_agent_runs, undx_tool_gateway

        run_id = self._queue()
        original = undx_tool_gateway.execute

        def boom(*a, **k):
            raise RuntimeError("still broken")

        undx_tool_gateway.execute = boom
        try:
            for _ in range(12):
                claimed = undx_agent_runs.claim_next(self.fx.cur, "worker_alpha")
                self.fx.commit()
                if not claimed:
                    break
                undx_agent_runs.execute_claimed(self.fx.cur, claimed, "worker_alpha")
                self.fx.commit()
        finally:
            undx_tool_gateway.execute = original

        row = self._row(run_id)
        self.assertIn(row["status"], {"dead_letter", "expired"})
        self.assertIsNone(undx_agent_runs.claim_next(self.fx.cur, "worker_alpha"))


class NothingAboutTheRunComesFromTheClient(unittest.TestCase):
    """Stage 15, asserted on the envelope rather than on the HTTP surface."""

    def setUp(self) -> None:
        self.fx = AgentFixture(**RUN_FLAGS).start()
        from services import undx_agent_runs

        self.runs = undx_agent_runs
        self.runs.ensure_schema(self.fx.cur)
        self.fx.commit()

    def tearDown(self) -> None:
        self.fx.stop()

    def test_enqueue_accepts_no_parameter_that_could_forge_execution_state(self) -> None:
        """The columns that decide what runs have one author each, and it is not a caller.

        Enumerated as a denial rather than by inspecting a successful call, because the
        risk is a *future* parameter: somebody adding ``status=`` for a test fixture, and
        a request body reaching it two releases later.
        """
        import inspect

        from services import undx_agent_runs

        accepted = set(inspect.signature(undx_agent_runs.enqueue).parameters)
        forbidden = {
            "status", "attempt_count", "max_attempts", "lease_owner", "lease_expires_at",
            "idempotency_key", "canonical_target_id", "arguments_hash", "expires_at",
            "confirmation_state", "outcome", "worker_id",
        }
        self.assertEqual(accepted & forbidden, set())

    def test_the_canonical_target_is_computed_and_never_supplied(self) -> None:
        """A caller able to name the target could point the approval at one row and the
        arguments at another, and every downstream reader — the binding check, the
        idempotency key, the audit trail — would side with the caller against the
        action."""
        from services import undx_capability_registry

        run_id = self.runs.enqueue(
            self.fx.cur, user_id=OWNER_ID, capability_id=A_READ,
            arguments={"canonical_target_id": "alert:99", "target_id": "alert:99"},
            confirmation_id="", client_request_id="req_target",
        )
        self.fx.commit()
        self.fx.cur.execute(
            "SELECT canonical_target_id FROM undx_agent_runs WHERE run_id=?", (run_id,))
        stored = str(dict(self.fx.cur.fetchone())["canonical_target_id"])

        spec = undx_capability_registry.get(A_READ)
        expected = spec.canonical_target(
            {"canonical_target_id": "alert:99", "target_id": "alert:99"})
        self.assertEqual(stored, expected)
        self.assertNotEqual(stored, "alert:99")

    def test_the_capability_must_exist_in_this_processs_registry(self) -> None:
        """Refused at the queue as well as at the gateway.

        A row naming a capability that does not exist can only ever be executed by a
        registry that gains it later, which is precisely the drift the refusal prevents.
        """
        from services.undx_agent_contracts import AgentError

        with self.assertRaises(AgentError) as caught:
            self.runs.enqueue(
                self.fx.cur, user_id=OWNER_ID, capability_id="pulsesoc.delete_everything",
                arguments={}, confirmation_id="", client_request_id="req_bad",
            )
        self.assertEqual(caught.exception.code, "unsupported_capability")

    def test_an_unauthenticated_owner_cannot_queue_anything(self) -> None:
        """Zero is the value an absent session produces, so it is the value that must
        refuse rather than default."""
        from services.undx_agent_contracts import AgentError

        for user_id in (0, -1):
            with self.subTest(user_id):
                with self.assertRaises(AgentError):
                    self.runs.enqueue(
                        self.fx.cur, user_id=user_id, capability_id=A_READ, arguments={},
                        confirmation_id="", client_request_id=f"req_{user_id}",
                    )

    def test_the_bounds_on_the_row_come_from_the_flag_surface(self) -> None:
        """Attempt caps are a property of the deployment, not of the request that queued
        the run. Fixed at enqueue so a later flag change cannot retroactively grant an
        in-flight run more tries than it was created with."""
        from services import undx_agent_runs

        run_id = self.runs.enqueue(
            self.fx.cur, user_id=OWNER_ID, capability_id=A_READ,
            arguments={"max_attempts": 9999}, confirmation_id="",
            client_request_id="req_bounds",
        )
        self.fx.commit()
        self.fx.cur.execute(
            "SELECT max_attempts, expires_at FROM undx_agent_runs WHERE run_id=?",
            (run_id,))
        row = dict(self.fx.cur.fetchone())
        self.assertEqual(int(row["max_attempts"]), undx_agent_runs.surface().max_attempts)
        self.assertNotEqual(int(row["max_attempts"]), 9999)
        self.assertTrue(str(row["expires_at"] or ""), "a run must carry a deadline")

    def test_the_queue_edge_passes_the_registry_capability_and_not_the_users_words(self) -> None:
        """Read at the call site, because this is the one guarantee ``enqueue`` cannot make
        for itself.

        ``_maybe_queue`` is handed a ``CapabilitySpec`` the deterministic resolver already
        chose. It passes ``spec.capability_id``. A capability becomes worker-backed because
        somebody added it to a list, never because a turn argued for it.
        """
        from services import undx_agent_runtime

        source = open(undx_agent_runtime.__file__, encoding="utf-8").read()
        body = source[source.index("def _maybe_queue("):]
        body = body[:body.index("\ndef ", 1)]
        self.assertIn("capability_id=spec.capability_id", body)
        self.assertIn("user_id=int(user_id)", body)
        self.assertIn('confirmation_id=""', body)
        # The eligibility decision is made from the registry entry and the resolved
        # target count. The person's sentence is not a parameter of it.
        self.assertNotIn("message", body.split("undx_worker_dispatch.decide(")[1][:400])


if __name__ == "__main__":
    unittest.main()
