"""What a run is told to say about itself, and the two ways that sentence goes wrong.

A durable run's stored status was chosen for the claim query. ``succeeded`` there means
the worker returned without raising — a statement about the worker, not about the person's
account. Handing it to a client unchanged is how a write that nobody could read back ends
up rendered as done, which is the one failure the whole verification chain exists to
prevent, arriving by the single route that chain does not watch.

So these tests are about the projection in :mod:`services.undx_run_status`, and they are
organised around the two directions it can be wrong in. Rounding *up* — reporting an
unconfirmed change as completed — is the dangerous one and gets the most tests. Rounding
*down* is the quieter one and gets its own class, because it is real: a successful read
reported as ``partial`` tells somebody their summary half-arrived, and a client that
hedges every answer teaches people to ignore the hedge, which costs the disclosure its
meaning on the day it matters.

The third class is the one that stops the model from being the thing that decides. Nothing
here consults a sentence, an intent or a confidence score; the projection reads a database
row and a registry entry, and a test that could pass with a plausible-sounding string in
place of a row would not be testing that.
"""

from __future__ import annotations

import os
import sys
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from tests.undx_agent import bootstrap

bootstrap.install()

from services import undx_run_status as run_status  # noqa: E402
from services.undx_run_status import RunStatus  # noqa: E402


#: A capability that changes the account, and one that only looks at it. Named rather
#: than inlined because the difference between them is the subject of half this file, and
#: a bare string at a call site does not say which of the two it is.
A_WRITE = "crypto.alerts.pause"
A_READ = "activity.daily_summary"


def _row(**fields):
    """A stored run row with only the columns a test cares about.

    Deliberately sparse. :func:`project` must read a partial row without raising, because
    a row written before an envelope column existed is exactly that, and a projection
    that needed every column would fail on the oldest rows in the table — the ones most
    likely to be asked about.
    """
    return dict(fields)


def _later(minutes: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat(
        timespec="seconds")


class NothingIsRoundedUp(unittest.TestCase):
    """No arrangement of a row reports a change as done unless a read-back confirmed it."""

    def test_a_finished_write_without_verification_is_partial_not_completed(self) -> None:
        """The dangerous pair, in its stored form.

        ``status='succeeded'`` and ``outcome='accepted_unverified'`` is a real row the
        worker writes: the executor ran, the mutation went out, and the independent
        read-back did not confirm it. Every word of that is true and none of it licenses
        "done". A projection that read only ``status`` would call this completed, and the
        person would be told their alert is paused on the strength of a gateway returning
        without an exception.
        """
        projection = run_status.project(
            _row(status="succeeded", outcome="accepted_unverified", capability_id=A_WRITE))

        self.assertEqual(projection.status, RunStatus.PARTIAL)
        self.assertFalse(projection.may_claim_completed)
        self.assertTrue(projection.requires_disclosure,
                        "a change that could not be read back must be stated with a hedge")

    def test_only_a_verified_write_reaches_completed_with_a_completion_claim(self) -> None:
        """The one row in the table that licenses telling somebody their change happened."""
        projection = run_status.project(
            _row(status="succeeded", outcome="verified_success", capability_id=A_WRITE))

        self.assertEqual(projection.status, RunStatus.COMPLETED)
        self.assertTrue(projection.may_claim_completed)
        self.assertFalse(projection.requires_disclosure)

    def test_no_unfinished_run_claims_completion(self) -> None:
        """Swept rather than sampled.

        Written as a loop over every non-terminal shape because the failure this guards
        is a branch added later that forgets to set the claim fields. A test naming three
        statuses would keep passing while a fourth was introduced beside them.
        """
        in_flight = [
            ("never attempted", _row(status="queued", attempt_count=0)),
            ("back in the queue", _row(status="queued", attempt_count=3)),
            ("a worker holds it", _row(status="running", lease_expires_at=_later(5))),
            ("the lease lapsed", _row(status="running", lease_expires_at=_later(-5))),
            ("parked on a person", _row(status="queued", confirmation_state="required")),
            ("unreadable", _row(status="something_new")),
        ]
        for label, row in in_flight:
            with self.subTest(label):
                projection = run_status.project(row)
                self.assertFalse(projection.may_claim_completed,
                                 f"{label} claimed a completed change")
                self.assertFalse(projection.terminal,
                                 f"{label} was reported as finished")

    def test_a_failed_run_is_never_softened(self) -> None:
        """Including ``dead_letter``, which is a queue's word and not an answer.

        A run that exhausted its attempts stopped without doing the thing. Reporting that
        as its own state would make the person decode a piece of queue vocabulary to
        learn that their request did not happen.
        """
        for stored in ("failed", "dead_letter"):
            with self.subTest(stored):
                projection = run_status.project(_row(status=stored))
                self.assertEqual(projection.status, RunStatus.FAILED)
                self.assertFalse(projection.may_claim_completed)
                self.assertTrue(projection.terminal)

    def test_an_unrecognised_status_is_unknown_and_is_not_terminal(self) -> None:
        """The fail-closed reading, and the reason it is not ``FAILED``.

        A row this module cannot read is a bug somewhere else. Both tempting ways to
        absorb it assert something unsupported: ``queued`` says the work is coming,
        ``failed`` says it will not. ``UNKNOWN`` says neither — and is deliberately not
        terminal, so a client polling the run keeps watching a run that may still be live.
        """
        projection = run_status.project(_row(status="wat"))

        self.assertEqual(projection.status, RunStatus.UNKNOWN)
        self.assertNotIn(RunStatus.UNKNOWN, run_status.TERMINAL_STATUSES)
        self.assertFalse(projection.terminal)
        self.assertTrue(projection.requires_disclosure)

    def test_a_projection_never_raises(self) -> None:
        """A read path that can throw is a read path that returns a 500 instead of a status.

        The caller has no better fallback available than this module's own, and inventing
        one at the call site is precisely where a status gets rounded up by somebody in a
        hurry.
        """
        for hostile in ({}, {"status": None}, {"status": 7}, {"status": "running",
                                                              "lease_expires_at": "not a date"}):
            with self.subTest(repr(hostile)):
                projection = run_status.project(hostile)
                self.assertIn(projection.status, run_status.ALL_STATUSES)
                self.assertFalse(projection.may_claim_completed)


class NothingIsRoundedDown(unittest.TestCase):
    """A hedge on every answer is a hedge that stops meaning anything."""

    def test_a_successful_read_is_completed_and_needs_no_hedge(self) -> None:
        """The case that motivated splitting the two claim fields.

        The Brain answers ``may_say_done=False`` for a finished read, and it is right to:
        a lookup completes no *change*, so there is nothing to report as done. But the
        request itself finished and answered. Deriving the status from ``may_say_done``
        alone would call this ``PARTIAL`` — "this ran, and the result could not be
        confirmed" — about a summary that arrived intact.

        ``requires_disclosure`` is what separates them, because it is ``False`` for a
        healthy read and a verified write alike and ``True`` for an unconfirmed one.
        """
        projection = run_status.project(
            _row(status="succeeded", outcome="verified_success", capability_id=A_READ))

        self.assertEqual(projection.status, RunStatus.COMPLETED)
        self.assertFalse(projection.requires_disclosure)
        self.assertFalse(projection.may_claim_completed,
                         "a read completes no change, so it claims none")

    def test_a_degraded_read_is_partial_because_it_is_not_the_whole_answer(self) -> None:
        """The other half: not every read that returned, returned everything."""
        projection = run_status.project(
            _row(status="succeeded", outcome="accepted_unverified", capability_id=A_READ))

        self.assertEqual(projection.status, RunStatus.PARTIAL)
        self.assertTrue(projection.requires_disclosure)

    def test_a_live_lease_is_running_not_waiting(self) -> None:
        """A worker is on it. Saying otherwise invites the person to re-ask.

        An unparseable or absent lease deadline is treated the same way, and that
        asymmetry is deliberate: calling a live worker lapsed tells somebody their request
        stalled while it is in fact executing, which is the error that produces a
        duplicate.
        """
        for label, lease in (("live", _later(5)), ("absent", ""), ("garbled", "soon")):
            with self.subTest(label):
                projection = run_status.project(
                    _row(status="running", lease_expires_at=lease))
                self.assertEqual(projection.status, RunStatus.RUNNING)

    def test_a_lapsed_lease_is_a_wait_and_not_a_failure(self) -> None:
        """The container that held this is gone; the run is still claimable.

        ``FAILED`` would be a lie about work that is going to be attempted again, and
        ``RUNNING`` would report a worker that no longer exists as busy on the person's
        behalf.
        """
        projection = run_status.project(
            _row(status="running", lease_expires_at=_later(-5)))

        self.assertEqual(projection.status, RunStatus.RETRY_WAIT)
        self.assertFalse(projection.terminal)

    def test_a_retried_run_is_distinguished_from_one_never_attempted(self) -> None:
        """Different news, and Stage 6's whole point.

        "Nobody has picked this up" and "somebody tried and it came back" lead to
        different next actions. Compressing them into one word — the word is usually
        "processing" — tells a person to keep waiting in both cases, including the one
        where waiting is the wrong thing to do.
        """
        fresh = run_status.project(_row(status="queued", attempt_count=0))
        retried = run_status.project(_row(status="queued", attempt_count=1))

        self.assertEqual(fresh.status, RunStatus.QUEUED)
        self.assertEqual(retried.status, RunStatus.RETRY_WAIT)
        self.assertNotEqual(fresh.status, retried.status)

    def test_a_run_parked_on_a_person_is_not_reported_as_queued(self) -> None:
        """It is not waiting for capacity. It is waiting for them.

        Checked ahead of the queued/running split for that reason: a person told their
        request is queued will wait for something that never arrives on its own.
        """
        projection = run_status.project(
            _row(status="queued", confirmation_state="required"))

        self.assertEqual(projection.status, RunStatus.WAITING_CONFIRMATION)


class TheModelDoesNotDecideThis(unittest.TestCase):
    """Every input to the projection is a stored fact or a registry entry."""

    def test_the_write_reading_comes_from_the_registry_not_the_row(self) -> None:
        """Two rows differing only in capability id project differently, and must.

        This is the assertion that the read/write distinction is looked up rather than
        stored, which matters because the row is written at enqueue and read after at
        least one deploy. A capability that became a write in the meantime must be held
        to a write's standard using the registry this process loaded, not the one that
        existed when the row was created.
        """
        write = run_status.project(
            _row(status="succeeded", outcome="accepted_unverified", capability_id=A_WRITE))
        read = run_status.project(
            _row(status="succeeded", outcome="verified_success", capability_id=A_READ))

        self.assertTrue(write.requires_disclosure)
        self.assertFalse(read.requires_disclosure)

    def test_an_unknown_capability_is_held_to_the_stricter_reading(self) -> None:
        """A capability the registry no longer declares is treated as a write.

        The two errors are not equal. Guessing "read" about a write reports an
        unconfirmed mutation as a completed one; guessing "write" about a read adds a
        hedge to a summary. Only one of those is a claim about somebody's account.
        """
        projection = run_status.project(
            _row(status="succeeded", outcome="accepted_unverified",
                 capability_id="capability.that.was.removed"))

        self.assertEqual(projection.status, RunStatus.PARTIAL)
        self.assertTrue(projection.requires_disclosure)

    def test_the_declared_vocabulary_covers_everything_project_can_return(self) -> None:
        """A status a client has never been told about is a status it renders as an error.

        ``ALL_STATUSES`` is sent to clients on every list response, so it has to be the
        superset rather than a list somebody kept in step by hand.
        """
        produced = {
            run_status.project(row).status
            for row in (
                _row(status="queued", attempt_count=0),
                _row(status="queued", attempt_count=1),
                _row(status="queued", confirmation_state="required"),
                _row(status="running", lease_expires_at=_later(5)),
                _row(status="running", lease_expires_at=_later(-5)),
                _row(status="succeeded", outcome="verified_success", capability_id=A_WRITE),
                _row(status="succeeded", outcome="accepted_unverified", capability_id=A_WRITE),
                _row(status="failed"), _row(status="dead_letter"),
                _row(status="cancelled"), _row(status="expired"), _row(status="?"),
            )
        }
        self.assertTrue(produced.issubset(set(run_status.ALL_STATUSES)))

    def test_the_states_this_substrate_cannot_yet_observe_are_declared_as_such(self) -> None:
        """The gap is data, so it is asserted rather than remembered.

        ``CLAIMED`` and ``VERIFYING`` are in the client vocabulary and are not producible:
        ``claim_next`` writes ``status='running'`` in the same statement that takes the
        lease, and verification happens inside the single gateway call. Declaring them
        anyway means a client written today already renders the states Stages 18-20 will
        start producing. Declaring them *and recording that they are unreachable* means
        the shortfall cannot be mistaken for a working feature.
        """
        self.assertEqual(run_status.UNOBSERVABLE_STATUSES,
                         {RunStatus.CLAIMED, RunStatus.VERIFYING})
        for status in run_status.UNOBSERVABLE_STATUSES:
            with self.subTest(status):
                self.assertIn(status, run_status.ALL_STATUSES)

    def test_every_status_has_a_sentence(self) -> None:
        """Because a client without one writes its own, and then there are two."""
        for status in run_status.ALL_STATUSES:
            with self.subTest(status):
                self.assertTrue(run_status.describe(status).strip())
        self.assertEqual(run_status.describe("not a status"),
                         run_status.describe(RunStatus.UNKNOWN))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
