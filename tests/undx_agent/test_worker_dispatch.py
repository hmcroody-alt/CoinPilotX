"""What may leave the request, and who is allowed to decide it.

Deferring an action means executing it after the person has stopped watching. That is a
useful property and a dangerous one, and the difference between the two is entirely a
question of *who chose to defer*. These tests assert that the chooser is the capability
registry and the flag surface — never the model, and never the person's phrasing.

The default under test is synchronous. A capability nobody has considered executes in the
request, exactly as every capability does today, so the failure mode of forgetting to
think about a new capability is "no change" rather than "silently moved out of sight".
"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from tests.undx_agent import bootstrap

bootstrap.install()

from services import undx_capability_registry  # noqa: E402
from services import undx_worker_dispatch as dispatch  # noqa: E402


RUNS_ON = {"UNDX_AGENT_RUNS_ENABLED": "1", "UNDX_WORKER_ENABLED": "1"}

#: A capability that is on the eligible list, and one that is not. Both are read-only, so
#: neither test is entangled with the write stop.
ELIGIBLE = "reels.performance.summary"
INELIGIBLE = "crypto.watchlist.list"


def spec(capability_id: str):
    found = undx_capability_registry.get(capability_id)
    assert found is not None, f"{capability_id} must exist for this test to mean anything"
    return found


class TheEligibleSetIsRealAndConservative(unittest.TestCase):

    def test_every_eligible_capability_is_registered(self) -> None:
        """A name in the set that is not in the registry is a rule about nothing.

        It would never match, so it would never route anything to the worker, and the
        list would drift into documentation of an intention rather than a description of
        behaviour — while still reading, at review time, as though the capability were
        covered.
        """
        missing = sorted(c for c in dispatch.WORKER_ELIGIBLE_CAPABILITIES
                         if undx_capability_registry.get(c) is None)
        self.assertEqual(missing, [])

    def test_no_write_is_worker_eligible_while_writes_are_stopped(self) -> None:
        """This work lands behind a global write stop.

        Listing a write here would enumerate a path that cannot be exercised and
        therefore cannot be proven, and an unprovable path in an allowlist is the kind of
        thing that gets believed later because it was written down earlier.
        """
        writes = sorted(c for c in dispatch.WORKER_ELIGIBLE_CAPABILITIES
                        if spec(c).is_write)
        self.assertEqual(writes, [])

    def test_the_set_is_a_small_deliberate_minority(self) -> None:
        """Most capabilities must stay synchronous, and that must be visible as a number.

        If this ever inverts, the default has quietly stopped being the default.
        """
        total = len(undx_capability_registry.REGISTRY)
        self.assertLess(len(dispatch.WORKER_ELIGIBLE_CAPABILITIES), total // 4)


class TheDecisionComesFromTheDescriptorNotFromProse(unittest.TestCase):

    def test_decide_does_not_accept_the_users_text(self) -> None:
        """Asserted on the signature, because this is the property the module exists for.

        A model that can set "run this in the background" can move any action out of the
        person's sight by describing it persuasively. ``decide`` cannot be persuaded
        because it is never told what was said.
        """
        import inspect

        parameters = set(inspect.signature(dispatch.decide).parameters)
        for forbidden in ("text", "question", "prose", "plan", "goal", "narration"):
            self.assertNotIn(forbidden, parameters)

    def test_an_eligible_read_is_worker_backed(self) -> None:
        decision = dispatch.decide(spec(ELIGIBLE), env=RUNS_ON)
        self.assertTrue(decision.worker_backed)
        self.assertEqual(decision.reason, dispatch.LONG_RUNNING_CAPABILITY)

    def test_an_ineligible_capability_stays_in_the_request(self) -> None:
        """A watchlist read completes in milliseconds. Queueing it would trade a correct
        immediate answer for a run row, a poll interval and a second round trip to learn
        what had already happened."""
        decision = dispatch.decide(spec(INELIGIBLE), env=RUNS_ON)
        self.assertTrue(decision.synchronous)
        self.assertEqual(decision.reason, dispatch.NOT_WORKER_ELIGIBLE)

    def test_a_fan_out_over_many_targets_is_reported_as_such(self) -> None:
        decision = dispatch.decide(spec(ELIGIBLE), resolved_count=12, env=RUNS_ON)
        self.assertTrue(decision.worker_backed)
        self.assertEqual(decision.reason, dispatch.BATCH_OVER_MANY_TARGETS)

    def test_fan_out_does_not_promote_an_ineligible_capability(self) -> None:
        """Forty likes are still forty milliseconds.

        A resolver that pins many targets for a fast capability has produced a fast
        batch, not a slow one, and letting count alone reach the queue would make the
        eligible set bypassable by asking for more of something cheap.
        """
        decision = dispatch.decide(spec(INELIGIBLE), resolved_count=40, env=RUNS_ON)
        self.assertTrue(decision.synchronous)
        self.assertEqual(decision.reason, dispatch.NOT_WORKER_ELIGIBLE)


class NothingIsQueuedWhenNothingCanClaimIt(unittest.TestCase):

    def test_runs_disabled_forces_synchronous_execution(self) -> None:
        """A queued action with no worker is an action that was accepted and then
        silently never happened — which is worse than a slow request, because the person
        is given a reason to stop waiting."""
        decision = dispatch.decide(spec(ELIGIBLE), env={})
        self.assertTrue(decision.synchronous)

    def test_the_reason_names_the_flag_that_stopped_it(self) -> None:
        """A dispatch decision is diagnostic evidence, so it must say which stop applied
        rather than collapsing every off-state into one word."""
        decision = dispatch.decide(spec(ELIGIBLE),
                                   env={"UNDX_AGENT_RUNS_ENABLED": "1",
                                        "UNDX_WORKER_ENABLED": "1",
                                        "UNDX_EMERGENCY_KILL_SWITCH": "1"})
        self.assertTrue(decision.synchronous)
        self.assertEqual(decision.reason, "emergency_kill_switch")


class AWriteIsConfirmedBeforeItIsQueuedNotAfter(unittest.TestCase):
    """The ordering the entire envelope depends on, tested on a stand-in.

    A real registered write cannot be used here without also depending on the live write
    stop, which would make the test pass for the wrong reason today and start failing for
    the wrong reason on the day the stop is lifted. The stand-in supplies only the two
    descriptor fields ``decide`` reads.
    """

    class Write:
        capability_id = "reels.performance.summary"   # eligible, so we test the later branch
        is_write = True
        confirmation = "always"

    def test_an_unconfirmed_write_is_returned_to_the_request(self) -> None:
        """Not lost — routed back to the human. Synchronous execution reaches the
        gateway, which mints the confirmation card the person has not yet seen."""
        from unittest import mock

        with mock.patch.object(dispatch.undx_agent_policy, "writes_available",
                               return_value=True):
            decision = dispatch.decide(self.Write(), has_confirmation=False, env=RUNS_ON)
        self.assertTrue(decision.synchronous)
        self.assertEqual(decision.reason, dispatch.AWAITING_CONFIRMATION)

    def test_a_confirmed_write_may_be_queued(self) -> None:
        from unittest import mock

        with mock.patch.object(dispatch.undx_agent_policy, "writes_available",
                               return_value=True):
            decision = dispatch.decide(self.Write(), has_confirmation=True, env=RUNS_ON)
        self.assertTrue(decision.worker_backed)

    def test_a_write_is_refused_to_its_face_while_writes_are_stopped(self) -> None:
        """The gateway would refuse this anyway. Refusing it here means the person is
        told now rather than being told a job was queued and having to return later to
        read a failure that was certain at the moment they asked."""
        from unittest import mock

        with mock.patch.object(dispatch.undx_agent_policy, "writes_available",
                               return_value=False):
            decision = dispatch.decide(self.Write(), has_confirmation=True, env=RUNS_ON)
        self.assertTrue(decision.synchronous)
        self.assertEqual(decision.reason, dispatch.WRITES_STOPPED)


class TheDispatchSurfaceCarriesNoSecrets(unittest.TestCase):

    def test_describe_reports_only_capability_names_and_a_count(self) -> None:
        surface = dispatch.describe()
        self.assertEqual(set(surface), {"worker_eligible_count", "worker_eligible"})
        self.assertEqual(surface["worker_eligible_count"], len(surface["worker_eligible"]))
        self.assertEqual(surface["worker_eligible"], sorted(surface["worker_eligible"]))


if __name__ == "__main__":
    unittest.main()
