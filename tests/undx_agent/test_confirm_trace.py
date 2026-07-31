"""An answer that says something went wrong has to be traceable too.

Batch 20 gave a dead confirmation button a sentence that names which kind of dead it is.
The sharpest of those sentences is the one for a spent approval:

    That confirmation was already used, so what it authorised has already been
    attempted. Check where things stand before confirming it again.

That sentence instructs the person to go and investigate. Until this batch it gave them
nothing to investigate *with*. The response carried no id, the server emitted no log
line, and no row was written anywhere — so a support conversation that began "I pressed
Confirm and it told me to check where things stand" had no thread to pull. From the
server's point of view the press did not happen.

The cause is ordinary and worth naming precisely, because it is the kind that spreads.
``confirm_action`` computes a ``correlation_id`` on its first line and has nine return
paths. Two of them put it in the body. The other seven — every refusal, plus the legacy
success payload — dropped it. Nobody decided that; each path was written on its own day
and the id was not in front of whoever wrote it.

So the fix is not "add the id to seven dictionaries". That would leave the tenth path,
whenever it is written, in exactly the same position. The body moved to
``_confirm_action`` and ``confirm_action`` became a wrapper that stamps whatever comes
back. :class:`APathNobodyHasWrittenYetTests` is the test that matters most here: it
asserts the property for a return path this file has never seen.

Three further things are asserted, each of which was separately wrong:

* **The refusal is recorded.** One log line, carrying the same id as the answer.
* **The refusal log does not contain the token.** A pending approval token is a live
  bearer credential. Adding logging to a rejection path is precisely the change that
  leaks one, so the absence is asserted rather than assumed — and the deliberate
  indistinguishability of unknown, foreign and spent tokens is asserted alongside it,
  because a log that distinguishes them would undo Batch 20's security property from
  behind.
* **The audit row shares the request's id.** ``record_tool_result`` was being handed a
  freshly minted ``_trace()`` — a second random id, for the audit row of an operation
  that already had one. An id nothing else shares is not a trace of anything.

:class:`TheLogLineReadsTheKeyThePayloadsCarryTests` covers the other end of the same
thread, in ``pulse_communications_v2/routes.py``. ``_timed_json`` logged
``payload.get("trace_id")``; ``pulse_ai_service`` emits ``"trace_id"`` zero times and
``"correlation_id"`` eleven. The single request-level log line for all 89 ``_timed_json``
endpoints therefore read ``trace_id=None``, and the route's own computed fallback was
dead code because these payloads are always dicts. The correct precedence chain was
already written twelve lines below, for the call-route warning, and simply was not
reused.
"""

from __future__ import annotations

import ast
import logging
import os
import pathlib
import sys
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from tests.undx_agent import bootstrap

bootstrap.install()

from tests.undx_agent.harness import AgentFixture, OTHER_ID, OUTSIDER_ID, OWNER_ID  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[2]
ROUTES = ROOT / "pulse_communications_v2" / "routes.py"
SERVICE = ROOT / "services" / "pulse_ai_service.py"

#: Turning the legacy V4/V5 executor on. Off by default in the fixture, which is what
#: production looks like wherever the agent runs, and which is the branch a dead
#: agent-minted approval actually reaches.
LEGACY_ON = {"UNDX_V4_ACTIONS": "1", "UNDX_V4_DISABLE_WRITES": ""}

#: The log record this batch adds. Named once so a rename has to be deliberate.
REFUSAL = "UNDX_CONFIRM_REFUSED"


class CapturedLog:
    """Collect log records emitted while the block runs, as formatted text.

    Records are formatted rather than inspected as arguments because what this batch is
    asserting about is the *line an operator reads*. A token that reaches the handler
    only through ``%s`` interpolation is still a token in the log file, and a test that
    looked at ``record.args`` alone could miss it.
    """

    def __init__(self) -> None:
        self.lines: list[str] = []

    def __enter__(self) -> "CapturedLog":
        outer = self

        class _Sink(logging.Handler):
            def emit(self, record: logging.LogRecord) -> None:
                outer.lines.append(record.getMessage())

        self._handler = _Sink(level=logging.DEBUG)
        self._root = logging.getLogger()
        self._saved_level = self._root.level
        self._root.setLevel(logging.DEBUG)
        self._root.addHandler(self._handler)
        return self

    def __exit__(self, *exc: object) -> None:
        self._root.removeHandler(self._handler)
        self._root.setLevel(self._saved_level)

    @property
    def text(self) -> str:
        return "\n".join(self.lines)

    def matching(self, needle: str) -> list[str]:
        return [line for line in self.lines if needle in line]


class ConfirmTraceBase(unittest.TestCase):

    FLAGS: dict[str, str] = {}

    def setUp(self) -> None:
        self.fx = AgentFixture(**self.FLAGS).start()
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
        """Mint one real agent approval by asking for a write in words."""
        response = self.runtime.handle(
            self.fx.cur, user_id=user_id, text="delete my bitcoin alert")
        self.fx.commit()
        self.assertEqual("confirmation_required", response.status)
        token = response.card.get("confirmation_token")
        self.assertTrue(token)
        return token

    def _confirm(self, token: str | None, user_id: int = OWNER_ID) -> dict:
        payload = {"confirmation_token": token} if token is not None else {}
        answer = self.svc.confirm_action(user_id, payload)
        self.fx.commit()
        return answer

    def assertTraceable(self, answer: dict) -> str:
        """Every answer, of every shape, has to name the request that produced it."""
        self.assertIsInstance(answer, dict)
        trace = answer.get("correlation_id")
        self.assertTrue(trace, f"no correlation_id on {answer.get('error') or answer.get('status')!r}")
        self.assertIsInstance(trace, str)
        return trace


class EveryAnswerCarriesTheIdTests(ConfirmTraceBase):
    """Nine return paths. Previously two were traceable."""

    def test_the_answer_that_says_go_and_check_carries_the_id_to_check_by(self):
        """The headline case, and the one whose own text makes the omission absurd.

        A spent approval tells the person to go and find out what happened. Before this
        batch that instruction shipped with no reference number, no log line and no row.
        """
        token = self._token()
        self.assertTrue(self._confirm(token)["ok"])
        replay = self._confirm(token)
        self.assertFalse(replay["ok"])
        self.assertEqual("confirmation_invalid", replay["error"])
        self.assertEqual(self.arch.APPROVAL_CONSUMED, replay["reason"])
        self.assertIn("Check where things stand", replay["message"])
        self.assertTraceable(replay)

    def test_a_lapsed_approval_carries_an_id(self):
        token = self._token()
        self.fx.cur.execute(
            "UPDATE pulse_ai_confirmations SET expires_at='2000-01-01T00:00:00+00:00'")
        self.fx.commit()
        answer = self._confirm(token)
        self.assertFalse(answer["ok"])
        self.assertEqual(self.arch.APPROVAL_EXPIRED, answer["reason"])
        self.assertTraceable(answer)

    def test_a_fabricated_token_carries_an_id(self):
        answer = self._confirm("undx_this_token_never_existed")
        self.assertFalse(answer["ok"])
        self.assertTraceable(answer)

    def test_a_missing_token_carries_an_id(self):
        """The 400 path, which never touches the database at all."""
        answer = self._confirm(None)
        self.assertFalse(answer["ok"])
        self.assertTraceable(answer)

    def test_a_successful_confirmation_carries_an_id(self):
        answer = self._confirm(self._token())
        self.assertTrue(answer["ok"])
        self.assertTraceable(answer)

    def test_two_requests_do_not_share_an_id(self):
        """An id every request shares identifies nothing."""
        first = self.assertTraceable(self._confirm("undx_nope_one"))
        second = self.assertTraceable(self._confirm("undx_nope_two"))
        self.assertNotEqual(first, second)


class APathNobodyHasWrittenYetTests(ConfirmTraceBase):
    """The property that survives the next person to add a ``return``.

    Asserting the seven paths individually would be a test of today's code. The reason
    the id went missing seven times is that each ``return`` was written separately, and
    a test that enumerates them reproduces exactly that. What is asserted here is the
    wrapper: an answer this file has never seen, from a body it has replaced, is stamped
    anyway.
    """

    def test_a_return_path_this_test_has_never_seen_is_traceable_anyway(self):
        original = self.svc._confirm_action
        seen: dict[str, str] = {}

        def invented(user_id, payload, correlation_id):
            seen["given"] = correlation_id
            return {"ok": False, "error": "a_refusal_invented_in_2027",
                    "message": "Something new went wrong.", "http_status": 418}

        self.svc._confirm_action = invented
        try:
            answer = self.svc.confirm_action(OWNER_ID, {"confirmation_token": "x"})
        finally:
            self.svc._confirm_action = original
        self.assertEqual("a_refusal_invented_in_2027", answer["error"])
        self.assertEqual(seen["given"], self.assertTraceable(answer))

    def test_a_path_that_names_its_own_id_keeps_it(self):
        """``setdefault``, not assignment.

        A payload carrying its own trace is describing something the wrapper did not do
        — a downstream call, a queued job — and overwriting it would destroy the only
        pointer to that thing. The wrapper fills a gap; it does not correct anybody.
        """
        original = self.svc._confirm_action
        self.svc._confirm_action = lambda u, p, c: {
            "ok": False, "error": "downstream", "correlation_id": "an-id-from-elsewhere"}
        try:
            answer = self.svc.confirm_action(OWNER_ID, {"confirmation_token": "x"})
        finally:
            self.svc._confirm_action = original
        self.assertEqual("an-id-from-elsewhere", answer["correlation_id"])

    def _spy_on_the_body(self, token: str | None, user_id: int = OWNER_ID):
        """Run one real request, capturing the id the body was handed."""
        seen: dict[str, str] = {}
        original = self.svc._confirm_action

        def spy(inner_user_id, payload, correlation_id):
            seen["inner"] = correlation_id
            return original(inner_user_id, payload, correlation_id)

        self.svc._confirm_action = spy
        try:
            answer = self.svc.confirm_action(
                user_id, {"confirmation_token": token} if token is not None else {})
        finally:
            self.svc._confirm_action = original
        self.fx.commit()
        return seen["inner"], answer

    def test_the_body_is_still_given_the_id_the_answer_is_stamped_with(self):
        """The wrapper fills a gap. It does not mint a competing id.

        ``_confirm_action`` hands its ``correlation_id`` to the gateway, to the audit
        write and to the two payloads that set it themselves. A wrapper that stamped a
        *freshly minted* id would satisfy every "the answer has an id" assertion while
        making that id match nothing — the defect being fixed, moved up a layer and made
        harder to see.

        Asserted on a **refusal**, which is the only place it is observable. On the
        success path ``_agent_confirm_payload`` has already set the key, so ``setdefault``
        does nothing and a second minted id would leave no trace. This test asserted the
        success path until ``mutate24.py``'s ``stamp_mints_a_second_id`` mode SURVIVED
        and said so.
        """
        inner, answer = self._spy_on_the_body("undx_this_token_never_existed")
        self.assertFalse(answer["ok"])
        self.assertEqual(inner, answer["correlation_id"])

    def test_the_body_and_the_answer_agree_on_the_success_path_too(self):
        inner, answer = self._spy_on_the_body(self._token())
        self.assertTrue(answer["ok"])
        self.assertEqual(inner, answer["correlation_id"])

    def test_the_refusal_log_carries_the_id_the_body_was_given(self):
        """The join has to survive to the log line, not just to the response body."""
        with CapturedLog() as log:
            inner, answer = self._spy_on_the_body("undx_this_token_never_existed")
        self.assertFalse(answer["ok"])
        self.assertIn(f"correlation_id={inner}", log.matching(REFUSAL)[0])


class TheRefusalIsRecordedTests(ConfirmTraceBase):
    """A rejected confirmation used to leave nothing behind on the server."""

    def test_a_refusal_emits_exactly_one_log_line(self):
        with CapturedLog() as log:
            answer = self._confirm("undx_this_token_never_existed")
        self.assertFalse(answer["ok"])
        self.assertEqual(1, len(log.matching(REFUSAL)), log.text)

    def test_the_log_line_carries_the_same_id_as_the_answer(self):
        """The join. Without this the log line and the response are two events."""
        with CapturedLog() as log:
            answer = self._confirm("undx_this_token_never_existed")
        line = log.matching(REFUSAL)[0]
        self.assertIn(f"correlation_id={answer['correlation_id']}", line)

    def test_the_log_line_names_the_shape_of_the_refusal(self):
        token = self._token()
        self.assertTrue(self._confirm(token)["ok"])
        with CapturedLog() as log:
            self._confirm(token)
        line = log.matching(REFUSAL)[0]
        self.assertIn("error=confirmation_invalid", line)
        self.assertIn(f"reason={self.arch.APPROVAL_CONSUMED}", line)
        self.assertIn("http_status=409", line)
        self.assertIn(f"user_id={OWNER_ID}", line)

    def test_the_refusal_log_never_contains_the_token(self):
        """The guard on this batch's own change.

        A pending approval token is a live bearer credential: anyone holding it can
        redeem the write. Adding logging to the rejection path is exactly the change
        that puts one in a log file, so this is asserted against a *live* token — the
        case where a leak would still be worth something — and not merely a dead one.
        """
        live = self._token()
        with CapturedLog() as log:
            self._confirm(live, user_id=OTHER_ID)  # foreign holder: refused, still live
        self.assertTrue(log.matching(REFUSAL))
        self.assertNotIn(live, log.text)
        self.assertNotIn(live[:16], log.text)

    def test_a_foreign_token_is_logged_identically_to_an_unknown_one(self):
        """Batch 20's indistinguishability must not be undone from behind.

        ``approval_state`` is owner-scoped so that a stranger cannot learn whether a
        guessed string names a real approval. A log line that said ``reason=consumed``
        for somebody else's spent token would re-disclose exactly that, to anybody who
        can read logs, for every probe an attacker cares to send.
        """
        token = self._token()
        self.assertTrue(self._confirm(token)["ok"])
        with CapturedLog() as foreign:
            self._confirm(token, user_id=OTHER_ID)
        with CapturedLog() as fictional:
            self._confirm("undx_this_token_never_existed", user_id=OTHER_ID)

        def shape(line: str) -> list[str]:
            return [part for part in line.split() if not part.startswith("correlation_id=")]

        self.assertEqual(shape(fictional.matching(REFUSAL)[0]),
                         shape(foreign.matching(REFUSAL)[0]))

    def test_a_successful_confirmation_is_not_logged_as_a_refusal(self):
        """A refusal log that fires on success is noise, and noise is not a trace."""
        with CapturedLog() as log:
            answer = self._confirm(self._token())
        self.assertTrue(answer["ok"])
        self.assertEqual([], log.matching(REFUSAL), log.text)


class TheAuditRowSharesTheRequestIdTests(ConfirmTraceBase):
    """``record_tool_result`` was handed a second random id.

    This is the legacy V4/V5 notification path, reached here through a user outside the
    agent cohort — which is the only way it is reachable at all, and is stated plainly
    rather than worked around, because the same defect on the agent path would be
    invisible to a test that quietly took the other branch.

    ``pulsesoc_notification_system`` is substituted for the duration. Not to avoid
    testing it — it has its own suite — but because it opens its own connection and
    writes through it, and ``_confirm_action`` is holding a write transaction on the
    same SQLite file at that moment. The lock is an artefact of one process doing what
    two normally do. What is under test is which id reaches the audit row.
    """

    FLAGS = dict(LEGACY_ON)

    def _stub_notifications(self, start: bool = False) -> dict:
        import services

        state = {"push": start}
        stub = types.ModuleType("services.pulsesoc_notification_system")
        stub.get_preferences = lambda uid: {
            "experience": {"enable_push_notifications": state["push"]}, "preferences": {}}

        def update(uid, payload):
            state["push"] = bool(payload.get("enable_push_notifications"))

        stub.update_preferences = update
        real = services.pulsesoc_notification_system
        services.pulsesoc_notification_system = stub
        sys.modules["services.pulsesoc_notification_system"] = stub
        self.addCleanup(lambda: setattr(services, "pulsesoc_notification_system", real))
        self.addCleanup(sys.modules.__setitem__, "services.pulsesoc_notification_system", real)
        return state

    def test_the_audit_row_carries_the_id_the_person_was_given(self):
        state = self._stub_notifications(start=False)
        made = self.arch.create_confirmation(self.fx.cur, OUTSIDER_ID, {
            "action_id": "notifications.preference.update", "target_id": "global",
            "arguments": {"category": "global", "push": True, "expected_current_push": False}})
        self.fx.commit()

        answer = self._confirm(made["confirmation_token"], user_id=OUTSIDER_ID)
        self.assertTrue(answer["ok"], answer)
        self.assertTrue(state["push"], "the write under audit did not happen")

        self.fx.cur.execute(
            "SELECT correlation_id, tool_name FROM pulse_ai_tool_operations "
            "ORDER BY id DESC LIMIT 1")
        row = dict(self.fx.cur.fetchone() or {})
        self.assertEqual("pulsesoc.notification_preferences.update", row.get("tool_name"))
        self.assertEqual(answer["correlation_id"], row.get("correlation_id"))


def _load_payload_trace():
    """Compile ``_payload_trace`` out of the shipped routes source, without Flask.

    ``pulse_communications_v2/routes.py`` imports Flask at module scope and Flask is not
    installed in this sandbox. Stubbing the whole web framework to reach one pure
    function would test the stub. This reads the real file, takes the real function
    definition by name, and executes that — so the thing asserted below is the source
    that ships, and a change to it changes these results.

    It is honest about its limits: it proves what the helper computes, not that
    ``_timed_json`` calls it. That second claim is asserted separately, and as a source
    assertion, in :meth:`TheLogLineReadsTheKeyThePayloadsCarryTests
    .test_the_timing_line_no_longer_reads_a_key_the_services_never_emit`.
    """
    tree = ast.parse(ROUTES.read_text())
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "_payload_trace":
            namespace: dict = {}
            exec(compile(ast.Module([node], []), str(ROUTES), "exec"), namespace)
            return namespace["_payload_trace"]
    raise AssertionError("_payload_trace is gone from routes.py")


class TheLogLineReadsTheKeyThePayloadsCarryTests(unittest.TestCase):
    """``trace_id=None`` on all 89 ``_timed_json`` endpoints, for as long as it existed."""

    def setUp(self) -> None:
        self.resolve = _load_payload_trace()

    def test_a_payload_carrying_only_correlation_id_resolves(self):
        """Presence, not precedence — and named so, after mistaking one for the other.

        This was called ``test_it_prefers_...`` until ``mutate24.py``'s
        ``resolver_prefers_the_key_nobody_emits`` mode SURVIVED against it. Swapping the
        precedence still returns ``abc123`` for a payload carrying nothing to prefer it
        over. A test whose name claims more than its body checks is worse than no test,
        because it is where somebody stops looking.
        """
        self.assertEqual("abc123", self.resolve({"correlation_id": "abc123"}, "fallback"))

    def test_the_older_name_still_resolves(self):
        self.assertEqual("older", self.resolve({"trace_id": "older"}, "fallback"))

    def test_correlation_id_wins_when_a_payload_carries_both(self):
        """The precedence assertion. The only payload shape that can observe it."""
        self.assertEqual(
            "preferred", self.resolve({"correlation_id": "preferred", "trace_id": "old"}, "fb"))

    def test_a_payload_carrying_neither_falls_back_to_the_id_the_route_minted(self):
        """The route's own id stopped being dead code the moment this chain existed."""
        self.assertEqual("routes-own-id", self.resolve({"ok": True}, "routes-own-id"))

    def test_a_payload_that_is_not_a_dict_falls_back(self):
        for payload in (None, "", [], 0, object()):
            self.assertEqual("routes-own-id", self.resolve(payload, "routes-own-id"))

    def test_an_empty_id_in_the_payload_does_not_win(self):
        """``""`` and ``None`` are absences, not values. Logging one prints nothing."""
        self.assertEqual("fb", self.resolve({"correlation_id": "", "trace_id": None}, "fb"))

    def test_it_never_returns_none(self):
        """The literal defect: the log line read ``trace_id=None``."""
        for payload in ({}, {"correlation_id": None}, {"trace_id": None}, None):
            self.assertIsInstance(self.resolve(payload, "fb"), str)
            self.assertNotEqual("None", self.resolve(payload, "fb"))

    def test_the_timing_line_no_longer_reads_a_key_the_services_never_emit(self):
        """A source assertion, and deliberately so.

        What broke was not the helper — the helper is new. It was the argument passed to
        one ``logging.info`` call, on a line no test could reach without a Flask request
        context and a running route. Asserting the source is a smaller claim than
        asserting behaviour, and this docstring is where that is admitted rather than
        somewhere it can be mistaken for more.
        """
        tree = ast.parse(ROUTES.read_text())
        timing = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Call) and node.args
            and isinstance(node.args[0], ast.Constant)
            and str(node.args[0].value).startswith("PULSE_COMM_V2_TIMING")
        ]
        self.assertEqual(1, len(timing), "expected exactly one timing log call")
        trace_argument = ast.unparse(timing[0].args[-1])
        self.assertEqual("_payload_trace(payload, trace_id)", trace_argument)

    def test_the_services_emit_correlation_id_and_not_trace_id(self):
        """The drift guard. The two ends of this thread have to keep the same name.

        If ``pulse_ai_service`` starts emitting ``trace_id`` instead, the fallback order
        above silently becomes wrong again, and nothing else in this file would notice.
        """
        source = SERVICE.read_text()
        self.assertGreater(source.count('"correlation_id"'), 5)
        self.assertEqual(0, source.count('"trace_id"'))


if __name__ == "__main__":
    unittest.main()
