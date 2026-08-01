"""The gate, tested at the point where letting somebody through would be convenient.

A rollout gate has one job and two ways to fail it. It can keep somebody out who should
be in, which is a support ticket. Or it can let somebody in who should be out, which is
a person receiving a feature nobody decided to give them — during the exact period when
the reason it is not given to everybody is that it is not known to be safe yet.

Every test here is written against the second failure. The recurring shape across this
whole package is a value that is stated, believed, and not load-bearing; ``qa_only``
was the purest instance of it, because a flag that is read by nothing produces no error
to notice.
"""

from __future__ import annotations

import os
import subprocess
import sys
import unittest
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from services.undx_brain import rollout as r  # noqa: E402


#: The master switch on. Every test that is about the *rollout* needs this, because the
#: master switch being off is a different refusal and would mask the one under test.
ON = {"UNDX_BRAIN_ENABLED": "1"}


def env(**overrides: str) -> dict[str, str]:
    """``ON`` plus overrides, so each test states only the flag it is about."""
    values = dict(ON)
    values.update(overrides)
    return values


OPEN_READS = env(UNDX_BRAIN_QA_ONLY="0", UNDX_BRAIN_ROLLOUT_PERCENT="100")


class TheFlagIsActuallyRead(unittest.TestCase):
    """The reason this module exists: ``UNDX_BRAIN_QA_ONLY`` used to decide nothing."""

    def test_qa_only_is_on_when_nobody_has_set_it(self):
        # The declared default. If this ever flips, a deploy that sets UNDX_BRAIN_ENABLED
        # and nothing else exposes the Brain to the whole userbase, and the deploy that
        # did it would look identical to the one before.
        self.assertTrue(r.surface(ON)["qa_only"])

    def test_a_stranger_is_refused_while_qa_only_is_on(self):
        verdict = r.may_read(4242, ON)
        self.assertFalse(verdict.allowed)
        self.assertIn("QA_ONLY", verdict.reason)

    def test_a_hundred_percent_rollout_does_not_survive_qa_only(self):
        # The whole point. An operator who left a percentage set from a previous
        # experiment and turned QA-only back on has asked for the experiment to stop.
        verdict = r.may_read(4242, env(UNDX_BRAIN_ROLLOUT_PERCENT="100"))
        self.assertFalse(verdict.allowed)
        self.assertIn("QA_ONLY", verdict.reason)

    def test_the_percentage_is_not_even_consulted_while_qa_only_is_on(self):
        # Not "consulted and overridden". The reported bucket is None, which is how the
        # refusal shows that no percentage comparison took place at all.
        self.assertIsNone(r.may_read(4242, env(UNDX_BRAIN_ROLLOUT_PERCENT="100")).bucket)
        self.assertFalse(r.surface(env(UNDX_BRAIN_ROLLOUT_PERCENT="100"))["percentages_consulted"])

    def test_turning_qa_only_off_is_what_lets_the_percentage_decide(self):
        self.assertTrue(r.may_read(4242, OPEN_READS).allowed)
        self.assertTrue(r.surface(OPEN_READS)["percentages_consulted"])


class TheMasterSwitchIsCheckedBeforeTheCohort(unittest.TestCase):
    def test_a_qa_member_is_still_refused_when_the_brain_is_off(self):
        verdict = r.may_read(42, {"UNDX_AGENT_QA_USER_IDS": "42", "UNDX_BRAIN_QA_ONLY": "0"})
        self.assertFalse(verdict.allowed)
        self.assertIn("UNDX_BRAIN_ENABLED", verdict.reason)

    def test_the_refusal_names_the_switch_rather_than_the_cohort(self):
        # Order matters for the person reading the log. "You are not in QA" sends an
        # operator to edit a user list; "the Brain is off" sends them to the flag that
        # is actually wrong.
        verdict = r.may_read(42, {"UNDX_BRAIN_ROLLOUT_PERCENT": "100", "UNDX_BRAIN_QA_ONLY": "0"})
        self.assertIn("UNDX_BRAIN_ENABLED", verdict.reason)
        self.assertNotIn("QA_ONLY", verdict.reason)


class AnEmptyCohortMeansNobody(unittest.TestCase):
    """The variable somebody forgot to set must not read as a full rollout."""

    def test_an_unset_cohort_admits_nobody(self):
        for account in (1, 2, 42, 999, 100000):
            with self.subTest(account=account):
                self.assertFalse(r.in_qa_cohort(account, ON))
                self.assertFalse(r.may_read(account, ON).allowed)

    def test_an_empty_string_cohort_admits_nobody(self):
        for raw in ("", "   ", ",", ", ,"):
            with self.subTest(raw=raw):
                self.assertFalse(r.in_qa_cohort(42, env(UNDX_AGENT_QA_USER_IDS=raw)))

    def test_a_named_member_is_admitted_and_a_neighbour_is_not(self):
        cohort = env(UNDX_AGENT_QA_USER_IDS="41, 42 ,43")
        self.assertTrue(r.may_read(42, cohort).allowed)
        self.assertTrue(r.may_read(41, cohort).allowed)
        self.assertFalse(r.may_read(44, cohort).allowed)

    def test_a_partial_match_is_not_a_match(self):
        # "4" must not be admitted by a cohort of "42", and "42" must not be admitted by
        # a cohort of "420". Substring membership is the classic version of this bug.
        self.assertFalse(r.in_qa_cohort(4, env(UNDX_AGENT_QA_USER_IDS="42")))
        self.assertFalse(r.in_qa_cohort(42, env(UNDX_AGENT_QA_USER_IDS="420")))
        self.assertFalse(r.in_qa_cohort(420, env(UNDX_AGENT_QA_USER_IDS="42")))

    def test_a_non_numeric_cohort_entry_admits_nobody_rather_than_everybody(self):
        # ``UNDX_AGENT_QA_USER_IDS=all`` is a plausible thing for somebody to type when
        # they want everyone. It must mean nobody, not everyone.
        for raw in ("all", "*", "everyone", "true"):
            with self.subTest(raw=raw):
                self.assertFalse(r.in_qa_cohort(42, env(UNDX_AGENT_QA_USER_IDS=raw)))

    def test_it_reuses_the_agent_cohort_rather_than_declaring_a_second_one(self):
        # Two QA lists is two things to keep in sync, and the failure mode of two lists
        # that disagree is somebody being in QA for half the system.
        from services import undx_agent_policy

        self.assertEqual(r.QA_USERS_ENV, undx_agent_policy.AGENT_QA_USERS_ENV)


class NobodyIsAMemberByAccident(unittest.TestCase):
    """An id that cannot be read is outside every cohort, never inside one."""

    def test_an_absent_id_reaches_nothing(self):
        for account in (None, 0, "", "   ", -1, "-5"):
            with self.subTest(account=account):
                self.assertIsNone(r.bucket(account))
                self.assertFalse(r.may_read(account, OPEN_READS).allowed)
                self.assertFalse(r.may_write(account, OPEN_READS).allowed)

    def test_true_is_not_account_one(self):
        # ``bool`` is an ``int`` subclass. A stray truthy flag arriving where an account
        # id belongs would otherwise be bucketed as account 1, an account that exists.
        self.assertIsNone(r.bucket(True))
        self.assertFalse(r.in_qa_cohort(True, env(UNDX_AGENT_QA_USER_IDS="1")))

    def test_a_float_is_refused_rather_than_truncated_into_another_account(self):
        # ``int(41.9)`` is 41. Same rule as the Memory Brain's owner id, and worth being
        # the same rule even though the consequence here is milder.
        for account in (41.9, 42.0, 3.7):
            with self.subTest(account=account):
                self.assertIsNone(r.bucket(account))
                self.assertFalse(r.may_read(account, OPEN_READS).allowed)

    def test_a_refusal_for_an_unreadable_id_reports_no_bucket(self):
        verdict = r.may_read(None, OPEN_READS)
        self.assertIsNone(verdict.bucket)
        self.assertIn("account id", verdict.reason)


class TheBucketIsStableEverywhere(unittest.TestCase):
    """A person who is inside the rollout must not fall out of it on the next request."""

    def test_the_same_account_lands_in_the_same_bucket_twice(self):
        self.assertEqual(r.bucket(4242), r.bucket(4242))

    def test_a_numeric_string_and_an_int_are_the_same_account(self):
        # The user id arrives as a string from a route parameter and as an int from the
        # database. If those bucketed differently, the same person would be inside the
        # rollout on one code path and outside it on another.
        self.assertEqual(r.bucket("4242"), r.bucket(4242))
        self.assertEqual(r.bucket(" 4242 "), r.bucket(4242))

    def test_the_bucket_survives_a_different_hash_seed(self):
        # This is the test that would have caught ``hash()``. Python randomises string
        # hashing per process, so an implementation built on it puts one account inside
        # the rollout on one gunicorn worker and outside it on the next — no error, no
        # log line, just an account that behaves differently on alternate requests.
        root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        script = (
            "import sys; sys.path.insert(0, %r);"
            "from services.undx_brain import rollout;"
            "print(','.join(str(rollout.bucket(i)) for i in range(1, 40)))" % root
        )
        seen = set()
        for seed in ("0", "1", "12345"):
            environment = dict(os.environ, PYTHONHASHSEED=seed)
            out = subprocess.run(
                [sys.executable, "-c", script],
                capture_output=True, text=True, env=environment, cwd=root, timeout=60,
            )
            self.assertEqual(out.returncode, 0, out.stderr)
            seen.add(out.stdout.strip())
        self.assertEqual(
            len(seen), 1,
            "bucketing changed between processes, so an account would flap in and out "
            "of the rollout between workers",
        )

    def test_consecutive_accounts_are_not_a_regular_comb(self):
        # ``user_id % 100`` is stable and still wrong: ids are sequential, so a one
        # percent rollout would select every hundredth signup, and *every*
        # percentage-gated feature would select the identical set of people.
        first_ten = [r.bucket(i) for i in range(1, 11)]
        self.assertNotEqual(first_ten, [i % r.BUCKETS for i in range(1, 11)])

    def test_the_population_spreads_across_every_bucket(self):
        # Not a statistical proof, just a guard against a digest that collapses. With
        # 20,000 accounts over 100 buckets the expected count is 200 each; a bucket that
        # is empty, or one holding a tenth of everybody, means the rollout percentage
        # does not mean what it says.
        counts = Counter(r.bucket(i) for i in range(1, 20001))
        self.assertEqual(len(counts), r.BUCKETS, "some bucket is unreachable")
        self.assertGreater(min(counts.values()), 100)
        self.assertLess(max(counts.values()), 320)


class ThePercentageMeansWhatItSays(unittest.TestCase):
    def test_zero_percent_admits_nobody(self):
        wide_open_except_the_dial = env(UNDX_BRAIN_QA_ONLY="0", UNDX_BRAIN_ROLLOUT_PERCENT="0")
        self.assertEqual(
            [i for i in range(1, 3000) if r.may_read(i, wide_open_except_the_dial).allowed], []
        )

    def test_a_hundred_percent_admits_everybody(self):
        self.assertEqual(
            [i for i in range(1, 3000) if not r.may_read(i, OPEN_READS).allowed], []
        )

    def test_a_bigger_percentage_never_removes_somebody(self):
        # Monotonic. Raising a rollout must not take the feature away from an account
        # that already had it — that is a person losing something mid-experiment, and
        # the operator raising the number would have no reason to expect it.
        admitted: set[int] = set()
        for percent in range(0, 101, 10):
            here = {
                i for i in range(1, 1500)
                if r.may_read(i, env(UNDX_BRAIN_QA_ONLY="0", UNDX_BRAIN_ROLLOUT_PERCENT=str(percent))).allowed
            }
            self.assertTrue(
                admitted <= here,
                f"raising the rollout to {percent} removed {sorted(admitted - here)[:5]}",
            )
            admitted = here

    def test_an_unparseable_percentage_is_nobody_rather_than_everybody(self):
        for raw in ("all", "one hundred", "", "  ", "100%", "0.99"):
            with self.subTest(raw=raw):
                verdict = r.may_read(4242, env(UNDX_BRAIN_QA_ONLY="0", UNDX_BRAIN_ROLLOUT_PERCENT=raw))
                self.assertFalse(verdict.allowed)

    def test_a_percentage_that_is_not_ascii_digits_is_refused(self):
        # ``int("٩٩")`` is 99 and ``int("１００")`` is 100 — Python accepts every Unicode
        # decimal digit, and ``str.isdigit`` agrees. So the naive parse turns a value a
        # reviewer cannot read into a full rollout. ``int("1_0_0")`` is 100 for a
        # different reason and with the same consequence: ``1_0`` beside ``10`` in a
        # dashboard is not a difference anybody would notice.
        for raw in ("٩٩", "１００", "𝟵𝟵", "1_0_0", "1 0 0"):
            with self.subTest(raw=raw):
                verdict = r.may_read(4242, env(UNDX_BRAIN_QA_ONLY="0", UNDX_BRAIN_ROLLOUT_PERCENT=raw))
                self.assertFalse(verdict.allowed)
                self.assertEqual(verdict.percent, 0)

    def test_an_account_id_that_is_not_ascii_digits_is_nobody(self):
        # Same parse, worse consequence: ``"٩٩"`` would be *account 99*, a real person,
        # reached through a string that does not spell their id.
        for raw in ("٩٩", "１００", "4_2"):
            with self.subTest(raw=raw):
                self.assertIsNone(r.bucket(raw))
                self.assertFalse(r.in_qa_cohort(raw, env(UNDX_AGENT_QA_USER_IDS="99,100,42")))

    def test_a_negative_percentage_is_zero_and_not_everyone(self):
        # A negative left of a ``<`` comparison would admit nobody by luck here, but the
        # clamp is what makes that true by construction rather than by the direction the
        # comparison happens to be written.
        verdict = r.may_read(4242, env(UNDX_BRAIN_QA_ONLY="0", UNDX_BRAIN_ROLLOUT_PERCENT="-40"))
        self.assertFalse(verdict.allowed)
        self.assertEqual(verdict.percent, 0)

    def test_an_oversized_percentage_is_a_hundred_and_not_an_error(self):
        verdict = r.may_read(4242, env(UNDX_BRAIN_QA_ONLY="0", UNDX_BRAIN_ROLLOUT_PERCENT="4000"))
        self.assertTrue(verdict.allowed)
        self.assertEqual(verdict.percent, 100)


class WritesNeverOutrunReads(unittest.TestCase):
    """The write cohort is a subset of the read cohort, by construction."""

    def test_nobody_gets_writes_without_reads(self):
        # The property, stated over the whole population rather than over an example.
        # A write path reached by somebody the read path refuses is UNDX changing an
        # account it was not cleared to look at.
        for reads, writes in (("10", "100"), ("0", "100"), ("25", "80"), ("50", "50")):
            settings = env(
                UNDX_BRAIN_QA_ONLY="0",
                UNDX_BRAIN_ROLLOUT_PERCENT=reads,
                UNDX_BRAIN_WRITES_ROLLOUT_PERCENT=writes,
            )
            with self.subTest(reads=reads, writes=writes):
                offenders = [
                    i for i in range(1, 2000)
                    if r.may_write(i, settings).allowed and not r.may_read(i, settings).allowed
                ]
                self.assertEqual(offenders, [], f"{len(offenders)} accounts could write but not read")

    def test_an_over_configured_write_percentage_is_capped_to_the_read_one(self):
        settings = env(
            UNDX_BRAIN_QA_ONLY="0",
            UNDX_BRAIN_ROLLOUT_PERCENT="10",
            UNDX_BRAIN_WRITES_ROLLOUT_PERCENT="50",
        )
        self.assertEqual(r.may_write(4242, settings).percent, 10)

    def test_the_cap_is_reported_rather_than_applied_silently(self):
        # A cap applied silently is indistinguishable, from the outside, from a cap that
        # was never applied — which is how a write rollout gets believed to be at 50.
        settings = env(
            UNDX_BRAIN_QA_ONLY="0",
            UNDX_BRAIN_ROLLOUT_PERCENT="10",
            UNDX_BRAIN_WRITES_ROLLOUT_PERCENT="50",
        )
        verdict = r.may_write(4242, settings)
        self.assertTrue(any("capped" in note for note in verdict.notes))
        snapshot = r.surface(settings)
        self.assertEqual(snapshot["writes_percent"], 50)
        self.assertEqual(snapshot["writes_percent_effective"], 10)
        self.assertTrue(snapshot["writes_percent_capped_by_reads"])

    def test_writes_default_to_nobody_even_with_reads_wide_open(self):
        # The two dials are independent in the direction that matters: opening reads to
        # everybody must not open writes to anybody.
        self.assertFalse(r.may_write(4242, OPEN_READS).allowed)
        self.assertTrue(r.may_read(4242, OPEN_READS).allowed)

    def test_a_qa_member_reaches_both_surfaces(self):
        # QA is the point of QA. A cohort that could read but not write would leave the
        # write path untested right up until it was turned on for strangers.
        cohort = env(UNDX_AGENT_QA_USER_IDS="42")
        self.assertTrue(r.may_read(42, cohort).allowed)
        self.assertTrue(r.may_write(42, cohort).allowed)

    def test_the_write_verdict_says_it_is_about_writes(self):
        self.assertEqual(r.may_write(42, ON).surface, "write")
        self.assertEqual(r.may_read(42, ON).surface, "read")


class TheRefusalExplainsItself(unittest.TestCase):
    def test_every_refusal_carries_a_reason(self):
        cases = [
            (42, {}),
            (42, ON),
            (None, OPEN_READS),
            (4242, env(UNDX_BRAIN_QA_ONLY="0")),
        ]
        for account, settings in cases:
            with self.subTest(account=account, settings=sorted(settings)):
                verdict = r.may_read(account, settings)
                self.assertFalse(verdict.allowed)
                self.assertTrue(verdict.reason.strip(), "a refusal with no reason is a shrug")

    def test_an_admission_carries_no_reason(self):
        # The reason field is for refusals. A populated reason on an allowed verdict
        # would be read, by anything scanning logs for refusals, as a refusal.
        self.assertEqual(r.may_read(4242, OPEN_READS).reason, "")

    def test_the_percentage_refusal_names_the_bucket_and_the_dial(self):
        settings = env(UNDX_BRAIN_QA_ONLY="0", UNDX_BRAIN_ROLLOUT_PERCENT="1")
        outside = next(i for i in range(1, 500) if not r.may_read(i, settings).allowed)
        verdict = r.may_read(outside, settings)
        self.assertIn(str(verdict.bucket), verdict.reason)
        self.assertIn("1 percent", verdict.reason)

    def test_the_verdict_is_usable_as_a_boolean(self):
        self.assertTrue(bool(r.may_read(4242, OPEN_READS)))
        self.assertFalse(bool(r.may_read(4242, ON)))

    def test_the_verdict_cannot_be_edited_after_the_fact(self):
        verdict = r.may_read(4242, ON)
        with self.assertRaises(Exception):
            verdict.allowed = True  # type: ignore[misc]


class TheSurfaceIsHonest(unittest.TestCase):
    def test_it_reports_the_dials_without_being_asked_about_a_person(self):
        snapshot = r.surface(env(
            UNDX_BRAIN_QA_ONLY="0",
            UNDX_BRAIN_ROLLOUT_PERCENT="25",
            UNDX_AGENT_QA_USER_IDS="42",
        ))
        self.assertTrue(snapshot["brain_enabled"])
        self.assertFalse(snapshot["qa_only"])
        self.assertTrue(snapshot["qa_cohort_configured"])
        self.assertEqual(snapshot["reads_percent"], 25)

    def test_an_unusable_cohort_is_reported_as_unconfigured(self):
        # "all" configures nobody, and a snapshot that called that "configured" would
        # tell an operator their cohort was set when it admits no one.
        self.assertFalse(r.surface(env(UNDX_AGENT_QA_USER_IDS="all"))["qa_cohort_configured"])

    def test_it_contains_no_account_ids(self):
        # The snapshot is for logs and admin screens. The cohort list is a set of real
        # people's account ids and does not belong in either.
        snapshot = r.surface(env(UNDX_AGENT_QA_USER_IDS="42,4242"))
        self.assertNotIn("42", str(snapshot.values()))


class TheFoundationMapNamesThisModule(unittest.TestCase):
    def test_qa_gating_names_the_gate_that_blocks(self):
        from services.undx_brain import foundation

        item = foundation.by_key("qa_gating")
        self.assertIsNotNone(item)
        self.assertIn(("services.undx_brain.rollout", "may_read"), item.owners)
        self.assertNotIn("declared and unread", item.gap)


if __name__ == "__main__":
    unittest.main()
