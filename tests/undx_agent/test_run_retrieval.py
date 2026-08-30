"""Reading a run back: whose runs you get, and what the row is allowed to tell you.

Queueing a run without a way to read it is worse than not queueing it, because the person
was told their request was accepted and then given no way to learn whether it happened.
:mod:`services.undx_agent_run_routes` is that way. These tests cover the two things a
retrieval surface gets wrong.

**Whose.** Every statement behind these endpoints carries the owner in its ``WHERE``
clause rather than filtering a fetched row afterwards. That is not a style preference: a
filter written after the fetch is a filter somebody can forget, and its failure mode is
handing a stranger's run to whoever guessed an id. The tests therefore attack the
statement — same run id, wrong account — rather than the handler.

**What.** A run row holds a live approval handle, the container that leased it, and the
argument body the person's own words produced. None of those belong in a response. The
column lists in :mod:`services.undx_agent_runs` are the first gate and ``_present`` is the
second, and the test below asserts against the *fields that must be absent* rather than
the fields that are present, because a leak is something appearing that nobody listed.

The HTTP handlers themselves are not exercised here. Reaching them means importing
``bot``, which is a 111k-line Flask monolith, and the properties worth defending live in
the SQL and in the projection rather than in the six lines of Flask between them. What is
asserted instead is that the module registers the two routes it claims to and reads
through the owner-scoped accessors — the seam where an ownership bug would actually live.
"""

from __future__ import annotations

import os
import sys
import unittest

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


class RetrievalBase(unittest.TestCase):
    def setUp(self) -> None:
        self.fx = AgentFixture(**RUN_FLAGS).start()
        from services import undx_agent_runs

        self.runs = undx_agent_runs
        self.runs.ensure_schema(self.fx.cur)
        self.fx.commit()

    def tearDown(self) -> None:
        self.fx.stop()

    def _queue(self, *, user_id=OWNER_ID, request_id="req_1", capability_id=A_READ,
               arguments=None):
        """A queued read, which needs no approval and so needs no fixture ceremony."""
        run_id = self.runs.enqueue(
            self.fx.cur, user_id=user_id, capability_id=capability_id,
            arguments=arguments if arguments is not None else {},
            confirmation_id="", client_request_id=request_id,
        )
        self.fx.commit()
        return run_id


class OnlyYourOwnRuns(RetrievalBase):
    """The owner is in the statement. There is no arrangement that loads a stranger's row."""

    def test_another_account_cannot_read_your_run_by_its_id(self) -> None:
        """The whole point of the surface, stated as the attack it prevents.

        A run id is not a secret — it travels to a phone, into logs, through support
        tickets. It must therefore not be a capability. Presenting a real, valid id under
        the wrong session loads nothing.
        """
        run_id = self._queue(user_id=OWNER_ID)

        self.assertIsNotNone(self.runs.get_for_user(self.fx.cur, OWNER_ID, run_id))
        self.assertIsNone(self.runs.get_for_user(self.fx.cur, OTHER_ID, run_id),
                          "a valid run id under the wrong account must load nothing")

    def test_a_missing_run_and_a_foreign_run_are_the_same_answer(self) -> None:
        """Both ``None``, on purpose.

        Distinguishing them would turn the detail endpoint into an oracle for whether an
        arbitrary run id is real. The only party that benefits from that distinction is
        one enumerating ids, and closing it costs nothing.
        """
        foreign = self._queue(user_id=OWNER_ID)

        self.assertEqual(
            self.runs.get_for_user(self.fx.cur, OTHER_ID, foreign),
            self.runs.get_for_user(self.fx.cur, OTHER_ID, "run_does_not_exist"),
        )

    def test_a_list_contains_only_the_asking_accounts_runs(self) -> None:
        """Scoped in the query rather than filtered after it.

        Both accounts have runs and the ids are distinct, so a listing that leaked would
        show it as an extra row rather than as a subtle field difference.
        """
        mine = {self._queue(user_id=OWNER_ID, request_id="req_a"),
                self._queue(user_id=OWNER_ID, request_id="req_b")}
        theirs = self._queue(user_id=OTHER_ID, request_id="req_c")

        listed = {str(row["run_id"]) for row in self.runs.for_user(self.fx.cur, OWNER_ID)}

        self.assertEqual(listed, mine)
        self.assertNotIn(theirs, listed)

    def test_an_id_shaped_like_an_injection_returns_nothing_rather_than_everything(self) -> None:
        """Parameterised, so this is a lookup for a run whose id is that literal string.

        Asserted anyway. The claim being defended is that the run id reaches a bound
        parameter and never string formatting, and the cheapest way to keep that true is
        a test that fails loudly the day somebody builds this statement with an f-string.
        """
        self._queue(user_id=OWNER_ID)

        for hostile in ("' OR '1'='1", "run_x' --", "%", "_"):
            with self.subTest(hostile):
                self.assertIsNone(
                    self.runs.get_for_user(self.fx.cur, OWNER_ID, hostile))


class TheRowDoesNotSayEverythingItKnows(RetrievalBase):
    """A run holds an approval handle and an argument body. Neither goes out."""

    #: Columns that exist on ``undx_agent_runs`` and must never reach a client.
    #: ``confirmation_id`` is most of what it takes to redeem a live approval;
    #: ``lease_owner`` names a container and is the operator's business; ``arguments_json``
    #: is the body the person's words produced, entering by one trust boundary and asked
    #: to leave by another.
    WITHHELD = ("confirmation_id", "lease_owner", "arguments_json", "receipt_json")

    def test_neither_accessor_returns_a_withheld_column(self) -> None:
        """Asserted against both readers, because "the detail view shows more" is exactly
        how the detail view ends up showing the confirmation id."""
        run_id = self._queue()

        detail = self.runs.get_for_user(self.fx.cur, OWNER_ID, run_id)
        listed = self.runs.for_user(self.fx.cur, OWNER_ID)[0]

        for column in self.WITHHELD:
            with self.subTest(column):
                self.assertNotIn(column, detail)
                self.assertNotIn(column, listed)

    def test_both_readers_return_the_same_shape(self) -> None:
        """One column list, shared, so a column added to the table cannot be added to one
        view and forgotten in the other."""
        run_id = self._queue()

        detail = self.runs.get_for_user(self.fx.cur, OWNER_ID, run_id)
        listed = self.runs.for_user(self.fx.cur, OWNER_ID)[0]

        self.assertEqual(set(detail), set(listed))
        self.assertEqual(set(detail), set(self.runs.READABLE_COLUMNS))

    def test_the_presented_payload_carries_the_hash_and_not_the_arguments(self) -> None:
        """The identity of the approved body without the body itself.

        ``arguments_hash`` is what makes "the person confirmed *this* request" checkable
        by a client or a support engineer. Echoing the arguments would add nothing to that
        and would send a resolved target list back out over a different boundary than the
        one it came in by.
        """
        from services import undx_agent_run_routes as pack

        run_id = self._queue(arguments={"window": "today"})
        payload = pack._present(self.runs.get_for_user(self.fx.cur, OWNER_ID, run_id))

        self.assertTrue(payload["arguments_hash"],
                        "the fingerprint of the approved body must be present")
        self.assertNotIn("arguments", payload)
        for column in self.WITHHELD:
            self.assertNotIn(column, payload)

    def test_a_queued_run_presents_as_queued_and_claims_nothing(self) -> None:
        """End to end over a real row: enqueue, read back, project.

        This is the assertion that the three modules agree. The row was written by
        ``enqueue``, read by ``get_for_user`` and projected by ``undx_run_status``, and a
        disagreement between any two of them shows up here as a status that claims
        progress the row does not support.
        """
        from services import undx_agent_run_routes as pack
        from services.undx_run_status import RunStatus

        run_id = self._queue()
        payload = pack._present(self.runs.get_for_user(self.fx.cur, OWNER_ID, run_id))

        self.assertEqual(payload["run_id"], run_id)
        self.assertEqual(payload["status"], RunStatus.QUEUED)
        self.assertFalse(payload["terminal"])
        self.assertFalse(payload["may_claim_completed"])
        self.assertEqual(payload["capability_id"], A_READ)


class TheSurfaceReadsAndDoesNotAct(unittest.TestCase):
    """Two GET routes over the owner-scoped accessors, and nothing else."""

    def test_it_registers_exactly_the_two_documented_read_routes(self) -> None:
        """A poll endpoint that can mutate is how a poll becomes a mutation.

        Asserted on the URL map rather than by reading the source, so a decorator added
        later — a ``POST`` cancel, a ``DELETE`` — fails this test rather than quietly
        joining a surface clients already hit every few seconds. Those belong on their own
        routes with their own authorisation, and that is a deliberate deferral rather than
        an omission.
        """
        from flask import Flask
        from services import undx_agent_run_routes as pack

        app = Flask(__name__)
        pack.register(app)
        undx = {str(rule): sorted(rule.methods - {"HEAD", "OPTIONS"})
                for rule in app.url_map.iter_rules() if "/api/undx/" in str(rule)}

        self.assertEqual(undx, {
            "/api/undx/runs": ["GET"],
            "/api/undx/runs/<run_id>": ["GET"],
        })

    def test_no_identity_or_target_is_taken_from_the_client(self) -> None:
        """Stage 15, asserted as absence rather than as rejection.

        A handler that reads ``user_id`` from the request and then validates it is one
        refactor away from reading it and trusting it. The parameter is not read at all,
        so there is nothing to validate and nothing to stop validating. The run id is the
        single client-supplied value, and it goes to a statement that already carries the
        owner.
        """
        import inspect
        from services import undx_agent_run_routes as pack

        source = inspect.getsource(pack)
        for forbidden in ("request.args.get(\"user_id\")",
                          "request.args.get(\"capability_id\")",
                          "request.args.get(\"target_id\")",
                          "request.json", "get_json"):
            with self.subTest(forbidden):
                self.assertNotIn(forbidden, source)

    def test_the_page_size_is_bounded_at_both_ends(self) -> None:
        """A missing limit is a default, not an error; an enormous one is clamped.

        Refusing a malformed limit would deny somebody their own run list over a client
        bug. Honouring an enormous one would make a list endpoint a way to pull an
        account's whole action history in a single round trip.
        """
        from flask import Flask
        from services import undx_agent_run_routes as pack

        app = Flask(__name__)
        cases = {"": pack.DEFAULT_LIMIT, "?limit=": pack.DEFAULT_LIMIT,
                 "?limit=abc": pack.DEFAULT_LIMIT, "?limit=0": 1, "?limit=-5": 1,
                 "?limit=5": 5, "?limit=999999": pack.MAX_LIMIT}
        for query, expected in cases.items():
            with self.subTest(query or "absent"):
                with app.test_request_context(f"/api/undx/runs{query}"):
                    self.assertEqual(pack._limit(), expected)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
