"""A remembered fact gets older, and a new one that disagrees says so out loud.

The test that carries the weight here is
:meth:`TheExistingStoreGetsThisBackwards.test_record_fact_flags_agreement_and_misses_disagreement`,
because it pins the behaviour the module exists to replace rather than the behaviour the
module adds. It runs the real :func:`services.undx_architecture.record_fact` against the
real schema and shows that the field named ``contradictions`` fills up when two sources
say the *same* thing and stays empty when a second observation says the threshold is a
different number. If that ever stops being true, this module's argument has changed and
the docstring making it is stale — which is exactly the case a test should catch.

Everything else divides in two. The ageing tests check that a fact past its trust
level's horizon can only be quoted with the time attached, that a fact with no
provenance cannot be quoted at all, and that no fact at any age or any trust is ever
citable as current state. The disagreement tests check the four outcomes and, more
importantly, that three of them set ``must_disclose`` — a resolved conflict that nobody
is told about is the failure being designed out, and it would pass every test that only
checked which value won.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from services import undx_architecture as architecture  # noqa: E402
from services.undx_brain import config as brain_config  # noqa: E402
from services.undx_brain import facts as f  # noqa: E402
from services.undx_brain import truth  # noqa: E402

#: The Brain on and fact ageing on. Both are needed: the master switch alone must not
#: turn this on, and a test that only set the second would pass against a module that
#: had forgotten to read the first.
ON = {"UNDX_BRAIN_ENABLED": "1", "UNDX_BRAIN_FACTS_ENABLED": "1"}

#: A fixed instant, so a horizon test does not become a test of how long the suite took
#: to run. Every observation below is expressed as an offset from it.
NOW = datetime(2026, 7, 31, 12, 0, 0, tzinfo=timezone.utc)


def ago(**offset) -> str:
    return (NOW - timedelta(**offset)).isoformat(timespec="seconds")


def observation(**overrides) -> f.Observation:
    """A live-verified reading of one alert's threshold, one minute old."""
    fields = dict(
        subject="crypto.alerts.7.threshold",
        value="50000",
        source="crypto.alerts.get",
        trust="live_verified",
        observed_at=ago(minutes=1),
        fact_id="undx_fact_stored",
    )
    fields.update(overrides)
    return f.Observation(**fields)


class TheFlagGovernsWhetherAnythingIsAnswered(unittest.TestCase):
    """Off means the module answers nothing, not that it found nothing wrong."""

    def test_reading_is_disabled_by_default(self):
        reading = f.read(observation(), now=NOW, env={})
        self.assertFalse(reading.ok)
        self.assertIs(reading.citability, f.Citability.NOT_CITABLE)
        self.assertIn("disabled", reading.reason)

    def test_comparison_is_disabled_by_default(self):
        outcome = f.compare(observation(), observation(value="60000"), env={})
        self.assertFalse(outcome.ok)
        self.assertFalse(outcome.must_disclose)
        self.assertIn("disabled", outcome.reason)

    def test_reconciliation_is_disabled_by_default(self):
        outcome = f.reconcile([observation()], observation(value="60000"), env={})
        self.assertFalse(outcome.ok)
        self.assertIn("disabled", outcome.reason)

    def test_the_master_switch_alone_does_not_turn_this_on(self):
        # A flag that activated on UNDX_BRAIN_ENABLED alone would mean enabling the
        # Brain enabled every unreleased stage inside it at once.
        reading = f.read(observation(), now=NOW, env={"UNDX_BRAIN_ENABLED": "1"})
        self.assertFalse(reading.ok)

    def test_the_flag_is_declared_in_the_catalog_and_defaults_off(self):
        declared = {flag.name: flag for flag in brain_config.CATALOG}
        self.assertIn("UNDX_BRAIN_FACTS_ENABLED", declared)
        flag = declared["UNDX_BRAIN_FACTS_ENABLED"]
        self.assertEqual(flag.default, "0")
        self.assertEqual(flag.fail, "closed")
        self.assertNotIn(
            "UNDX_BRAIN_FACTS_ENABLED",
            brain_config.unknown_undx_brain_vars({"UNDX_BRAIN_FACTS_ENABLED": "1"}),
            "the flag must be recognised by the typo detector, not reported as a typo",
        )


class AFactGetsOlder(unittest.TestCase):
    """Age is measured against the horizon its trust level earns, and reported."""

    def test_a_fresh_live_reading_may_be_stated_without_a_time(self):
        reading = f.read(observation(observed_at=ago(minutes=1)), now=NOW, env=ON)
        self.assertIs(reading.citability, f.Citability.RECORDED)
        self.assertTrue(reading)
        self.assertFalse(reading.stale)
        self.assertEqual(reading.qualifier, "")
        self.assertEqual(reading.horizon_seconds, 900)

    def test_the_same_reading_twenty_minutes_later_may_only_be_stated_as_of(self):
        reading = f.read(observation(observed_at=ago(minutes=20)), now=NOW, env=ON)
        self.assertIs(reading.citability, f.Citability.AS_OF)
        self.assertFalse(reading)
        self.assertTrue(reading.stale)
        self.assertIn("as of", reading.qualifier)
        self.assertIn("2026-07-31T11:40:00+00:00", reading.qualifier)
        self.assertIn(reading.qualifier, reading.citation)

    def test_the_citation_always_carries_the_hedge_its_trust_level_obliges(self):
        for level in truth.TrustLevel:
            with self.subTest(trust=level.value):
                reading = f.read(
                    observation(trust=level.value, observed_at=ago(minutes=1)),
                    now=NOW, env=ON,
                )
                self.assertTrue(reading.citation, "a citation must never be empty")
                if truth.rank(level) > 0:
                    self.assertIn(truth.hedge_for(level), reading.citation)

    def test_every_trust_level_has_a_horizon_and_none_is_negative(self):
        # A level added to TrustLevel without a horizon would silently inherit zero
        # through the ``.get`` default, which reads as "always stale" and looks like a
        # deliberate decision. Making it a missing key here makes it a decision.
        for level in truth.TrustLevel:
            with self.subTest(trust=level.value):
                self.assertIn(level, f.HORIZON_SECONDS)
                self.assertGreaterEqual(f.HORIZON_SECONDS[level], 0)

    def test_the_two_levels_reached_by_looking_at_a_running_system_expire_soonest(self):
        # The inversion is deliberate and documented, so it is pinned. Somebody
        # "correcting" it to reward trust with a longer horizon breaks this.
        live = f.HORIZON_SECONDS[truth.TrustLevel.LIVE_VERIFIED]
        canonical = f.HORIZON_SECONDS[truth.TrustLevel.RUNTIME_CANONICAL]
        mapped = f.HORIZON_SECONDS[truth.TrustLevel.SOURCE_MAPPED]
        self.assertLess(canonical, live)
        self.assertLess(live, mapped)

    def test_a_blocked_or_unknown_provenance_fact_is_not_quotable_at_all(self):
        for trust in ("", "blocked", "invented_by_a_later_generator"):
            with self.subTest(trust=trust):
                reading = f.read(observation(trust=trust), now=NOW, env=ON)
                self.assertTrue(reading.ok)
                self.assertIs(reading.citability, f.Citability.NOT_CITABLE)
                self.assertEqual(reading.horizon_seconds, 0)

    def test_a_deprecated_fact_is_time_qualified_on_the_day_it_is_written(self):
        reading = f.read(
            observation(trust="deprecated", observed_at=ago(seconds=1)), now=NOW, env=ON
        )
        self.assertIs(reading.citability, f.Citability.AS_OF)

    def test_an_unreadable_timestamp_is_not_read_as_recent(self):
        for stamp in ("", "yesterday", "not-a-date"):
            with self.subTest(stamp=stamp):
                reading = f.read(observation(observed_at=stamp), now=NOW, env=ON)
                self.assertIs(reading.citability, f.Citability.NOT_CITABLE)
                self.assertIsNone(
                    reading.age_seconds,
                    "an unknown age must be None, never zero — zero renders as 'just now'",
                )

    def test_a_timestamp_without_an_offset_is_time_qualified_however_new_it_looks(self):
        reading = f.read(observation(observed_at="2026-07-31T11:59:00"), now=NOW, env=ON)
        self.assertIs(reading.citability, f.Citability.AS_OF)
        self.assertTrue(any("offset" in note for note in reading.notes))

    def test_a_timestamp_from_the_future_is_not_treated_as_brand_new(self):
        reading = f.read(observation(observed_at=ago(minutes=-30)), now=NOW, env=ON)
        self.assertIs(reading.citability, f.Citability.AS_OF)
        self.assertLess(reading.age_seconds, 0)
        self.assertIn("future", reading.reason)

    def test_a_z_suffixed_timestamp_is_understood(self):
        # Python 3.10's fromisoformat does not accept "Z"; a fact written by anything
        # that emits it would otherwise be permanently uncitable for a formatting reason.
        reading = f.read(observation(observed_at="2026-07-31T11:59:00Z"), now=NOW, env=ON)
        self.assertIs(reading.citability, f.Citability.RECORDED)
        self.assertEqual(reading.age_seconds, 60.0)


class NothingStoredIsEverCurrent(unittest.TestCase):
    """The ceiling, checked at every level and every age rather than asserted once."""

    def test_no_fact_at_any_trust_or_any_age_may_be_cited_as_current_state(self):
        for level in truth.TrustLevel:
            for label, stamp in (("fresh", ago(seconds=1)), ("old", ago(days=400))):
                with self.subTest(trust=level.value, age=label):
                    reading = f.read(
                        observation(trust=level.value, observed_at=stamp), now=NOW, env=ON
                    )
                    self.assertFalse(reading.may_cite_as_current)

    def test_the_ceiling_is_computed_from_truth_rather_than_written_here(self):
        # If ``may_claim_live_state`` ever starts returning True for some level, this
        # module must change with it rather than keep a hard-coded False that has
        # quietly stopped agreeing with the module it claims to defer to.
        for level in truth.TrustLevel:
            with self.subTest(trust=level.value):
                self.assertEqual(
                    f.read(observation(trust=level.value), now=NOW, env=ON).may_cite_as_current,
                    truth.may_claim_live_state(level),
                )


class TheExistingStoreGetsThisBackwards(unittest.TestCase):
    """The premise of the module, checked against the real schema rather than asserted.

    ``record_fact`` compares claim *text*, so two rows that disagree are by construction
    different strings and never meet. These tests run it for real.
    """

    def setUp(self):
        self.connection = sqlite3.connect(":memory:")
        self.connection.row_factory = sqlite3.Row
        self.cursor = self.connection.cursor()
        architecture.ensure_schema(self.cursor)

    def tearDown(self):
        self.connection.close()

    def test_record_fact_flags_agreement_and_misses_disagreement(self):
        first = architecture.record_fact(
            self.cursor, "btc alert threshold is 50000", "crypto.alerts.get", 0.9, 7)
        corroboration = architecture.record_fact(
            self.cursor, "btc alert threshold is 50000", "user_statement", 0.5, 7)
        disagreement = architecture.record_fact(
            self.cursor, "btc alert threshold is 60000", "crypto.alerts.get", 0.9, 7)

        self.assertEqual(first["contradictions"], [])
        self.assertEqual(
            corroboration["contradictions"], [first["fact_id"]],
            "two sources agreeing is what the existing store calls a contradiction",
        )
        self.assertEqual(corroboration["status"], "review")
        self.assertEqual(
            disagreement["contradictions"], [],
            "a different value for the same thing is the disagreement, and it is missed",
        )
        self.assertEqual(disagreement["status"], "active")

        self.cursor.execute("SELECT claim FROM pulse_ai_truth_facts WHERE status='active'")
        claims = sorted(row["claim"] for row in self.cursor.fetchall())
        self.assertIn("btc alert threshold is 50000", claims)
        self.assertIn("btc alert threshold is 60000", claims)

    def test_the_new_module_reaches_the_opposite_conclusion_on_the_same_pair(self):
        stored = observation(value="50000", trust="live_verified", observed_at=ago(hours=2))
        corroborating = observation(
            value="50000", source="user_statement", trust="documented",
            observed_at=ago(minutes=1), fact_id="undx_fact_new")
        conflicting = observation(
            value="60000", observed_at=ago(minutes=1), fact_id="undx_fact_new")

        self.assertIs(
            f.compare(stored, corroborating, env=ON).resolution, f.Resolution.AGREEMENT)
        self.assertIs(
            f.compare(stored, conflicting, env=ON).resolution, f.Resolution.PREFER_NEW)

    def test_record_fact_without_metadata_writes_exactly_what_it_always_wrote(self):
        architecture.record_fact(self.cursor, "a claim", "a source", 0.5, 7)
        self.cursor.execute("SELECT metadata_json FROM pulse_ai_truth_facts")
        self.assertEqual(
            self.cursor.fetchone()["metadata_json"], '{"contradiction_count": 0}')

    def test_metadata_makes_a_written_fact_comparable_and_cannot_forge_the_count(self):
        architecture.record_fact(
            self.cursor, "btc alert threshold is 50000", "crypto.alerts.get", 0.9, 7)
        architecture.record_fact(
            self.cursor, "btc alert threshold is 50000", "user_statement", 0.5, 7,
            metadata={
                f.SUBJECT_KEY: "crypto.alerts.7.threshold",
                f.TRUST_KEY: "documented",
                "contradiction_count": 0,
            },
        )
        self.cursor.execute(
            "SELECT * FROM pulse_ai_truth_facts WHERE source='user_statement'")
        row = self.cursor.fetchone()
        stored = json.loads(row["metadata_json"])
        self.assertEqual(stored["contradiction_count"], 1, "the count is not the caller's")
        self.assertEqual(stored[f.SUBJECT_KEY], "crypto.alerts.7.threshold")

        recovered = f.from_row(row)
        self.assertTrue(recovered.comparable)
        self.assertEqual(recovered.subject, "crypto.alerts.7.threshold")
        self.assertEqual(recovered.trust, "documented")
        self.assertEqual(recovered.observed_at, row["valid_from"])

    def test_a_row_written_the_old_way_is_honestly_reported_as_uncomparable(self):
        architecture.record_fact(
            self.cursor, "btc alert threshold is 50000", "crypto.alerts.get", 0.9, 7)
        self.cursor.execute("SELECT * FROM pulse_ai_truth_facts")
        legacy = f.from_row(self.cursor.fetchone())

        self.assertFalse(legacy.comparable)
        self.assertEqual(legacy.trust, "", "confidence is not a trust level")
        self.assertIs(
            f.read(legacy, now=NOW, env=ON).citability, f.Citability.NOT_CITABLE)
        self.assertIs(
            f.compare(legacy, observation(value="60000"), env=ON).resolution,
            f.Resolution.UNCOMPARABLE,
        )

    def test_from_row_survives_a_malformed_metadata_blob(self):
        self.assertEqual(f.from_row({"metadata_json": "{not json"}).subject, "")
        self.assertEqual(f.from_row({"metadata_json": "[1, 2]"}).subject, "")
        self.assertEqual(f.from_row(None).subject, "")


class ADisagreementIsReported(unittest.TestCase):
    """Four outcomes, and the one property common to three of them."""

    def test_a_newer_observation_of_equal_trust_supersedes_and_says_so(self):
        stored = observation(value="50000", observed_at=ago(hours=2))
        outcome = f.compare(stored, observation(value="60000"), env=ON)
        self.assertIs(outcome.resolution, f.Resolution.PREFER_NEW)
        self.assertEqual(outcome.supersedes, "undx_fact_stored")
        self.assertTrue(outcome.must_disclose)
        self.assertIn("50000", outcome.disclosure)
        self.assertIn("60000", outcome.disclosure)

    def test_a_newer_but_weaker_observation_does_not_overturn_and_is_still_reported(self):
        stored = observation(value="50000", trust="live_verified", observed_at=ago(hours=2))
        weaker = observation(value="60000", trust="documented", source="user_statement")
        outcome = f.compare(stored, weaker, env=ON)
        self.assertIs(outcome.resolution, f.Resolution.KEEP_STORED)
        self.assertEqual(outcome.supersedes, "")
        self.assertTrue(
            outcome.must_disclose,
            "keeping the stored value is a resolution, not a reason to stay quiet",
        )

    def test_a_stronger_observation_of_an_earlier_moment_settles_nothing(self):
        stored = observation(value="50000", observed_at=ago(hours=2))
        older = observation(value="60000", observed_at=ago(hours=9))
        outcome = f.compare(stored, older, env=ON)
        self.assertIs(outcome.resolution, f.Resolution.UNRESOLVED)
        self.assertTrue(outcome.must_disclose)

    def test_two_observations_at_the_same_instant_are_unresolved(self):
        stamp = ago(minutes=1)
        outcome = f.compare(
            observation(value="50000", observed_at=stamp),
            observation(value="60000", observed_at=stamp),
            env=ON,
        )
        self.assertIs(outcome.resolution, f.Resolution.UNRESOLVED)

    def test_a_disagreement_with_no_provenance_on_either_side_is_unresolved(self):
        outcome = f.compare(
            observation(value="50000", trust="", observed_at=ago(hours=2)),
            observation(value="60000", trust=""),
            env=ON,
        )
        self.assertIs(outcome.resolution, f.Resolution.UNRESOLVED)
        self.assertTrue(outcome.must_disclose)

    def test_an_unreadable_timestamp_prevents_supersession_rather_than_permitting_it(self):
        outcome = f.compare(
            observation(value="50000", observed_at="whenever"),
            observation(value="60000"),
            env=ON,
        )
        self.assertIs(outcome.resolution, f.Resolution.UNRESOLVED)
        self.assertEqual(outcome.supersedes, "")

    def test_agreement_is_the_only_outcome_that_discloses_nothing(self):
        pairs = (
            (observation(value="50000", observed_at=ago(hours=2)), observation(value="50000")),
            (observation(value="50000", observed_at=ago(hours=2)), observation(value="60000")),
            (observation(value="50000", trust="live_verified", observed_at=ago(hours=2)),
             observation(value="60000", trust="documented")),
            (observation(value="50000", observed_at=ago(hours=2)),
             observation(value="60000", observed_at=ago(hours=9))),
        )
        for stored, new in pairs:
            outcome = f.compare(stored, new, env=ON)
            with self.subTest(resolution=outcome.resolution.value):
                self.assertEqual(
                    outcome.must_disclose, outcome.resolution is not f.Resolution.AGREEMENT)
                self.assertEqual(bool(outcome), outcome.must_disclose)

    def test_values_are_compared_after_normalisation_not_by_exact_string(self):
        outcome = f.compare(
            observation(value="  50000 ", observed_at=ago(hours=2)),
            observation(value="50000"),
            env=ON,
        )
        self.assertIs(outcome.resolution, f.Resolution.AGREEMENT)

    def test_different_subjects_are_not_a_disagreement(self):
        outcome = f.compare(
            observation(subject="crypto.alerts.7.threshold", observed_at=ago(hours=2)),
            observation(subject="crypto.alerts.9.threshold", value="60000"),
            env=ON,
        )
        self.assertIs(outcome.resolution, f.Resolution.UNCOMPARABLE)
        self.assertFalse(outcome.must_disclose)

    def test_a_superseded_fact_with_no_id_is_reported_as_unnameable(self):
        outcome = f.compare(
            observation(value="50000", observed_at=ago(hours=2), fact_id=""),
            observation(value="60000"),
            env=ON,
        )
        self.assertIs(outcome.resolution, f.Resolution.PREFER_NEW)
        self.assertEqual(outcome.supersedes, "")
        self.assertTrue(any("fact_id" in note for note in outcome.notes))


class ReconcilingAgainstEverythingStored(unittest.TestCase):
    """Several stored facts, one new observation, and one answer to act on."""

    def test_the_most_cautious_outcome_wins(self):
        stale_but_equal = observation(value="50000", observed_at=ago(hours=2), fact_id="a")
        simultaneous = observation(value="50000", observed_at=ago(minutes=1), fact_id="b")
        new = observation(value="60000", observed_at=ago(minutes=1), fact_id="new")

        outcome = f.reconcile([stale_but_equal, simultaneous], new, env=ON)
        self.assertIs(outcome.resolution, f.Resolution.UNRESOLVED)
        self.assertTrue(outcome.must_disclose)
        self.assertEqual(len(outcome.disagreements), 2)

    def test_agreements_elsewhere_do_not_cancel_a_conflict(self):
        agreeing = observation(value="60000", observed_at=ago(hours=3), fact_id="a")
        conflicting = observation(value="50000", observed_at=ago(hours=2), fact_id="b")
        outcome = f.reconcile(
            [agreeing, conflicting], observation(value="60000", fact_id="new"), env=ON)
        self.assertIs(outcome.resolution, f.Resolution.PREFER_NEW)
        self.assertEqual(outcome.supersedes, ("b",))
        self.assertTrue(outcome.must_disclose)

    def test_uncomparable_rows_are_listed_rather_than_counted_as_conflicts(self):
        legacy = f.Observation(
            subject="", value="btc alert threshold is 50000", trust="live_verified",
            observed_at=ago(hours=2), fact_id="undx_fact_legacy")
        outcome = f.reconcile([legacy], observation(value="60000"), env=ON)
        self.assertIs(outcome.resolution, f.Resolution.NOTHING_TO_COMPARE)
        self.assertEqual(outcome.uncomparable, ("undx_fact_legacy",))
        self.assertFalse(outcome.must_disclose)
        self.assertIn("declare no subject", outcome.reason)

    def test_an_empty_store_is_nothing_to_compare_rather_than_agreement(self):
        outcome = f.reconcile([], observation(), env=ON)
        self.assertIs(outcome.resolution, f.Resolution.NOTHING_TO_COMPARE)
        self.assertFalse(outcome.must_disclose)

    def test_every_resolution_has_a_caution_rank(self):
        # ``reconcile`` picks the most cautious outcome by looking each one up. A member
        # added to Resolution without a rank would raise KeyError inside a function
        # documented as never raising.
        for resolution in f.Resolution:
            with self.subTest(resolution=resolution.value):
                self.assertIn(resolution, f._CAUTION)


class NothingHereRaises(unittest.TestCase):
    """Every entry point is called by code with no fallback of its own."""

    def test_garbage_in_every_field_still_returns_an_answer(self):
        junk = (None, 0, [], {}, object(), "\x00")
        for value in junk:
            with self.subTest(value=repr(value)):
                candidate = f.Observation(
                    subject=str(value), value=str(value), trust=value,  # type: ignore[arg-type]
                    observed_at=value,  # type: ignore[arg-type]
                )
                self.assertIsInstance(f.read(candidate, env=ON), f.Reading)
                self.assertIsInstance(f.compare(candidate, candidate, env=ON), f.Disagreement)
                self.assertIsInstance(
                    f.reconcile([candidate], candidate, env=ON), f.Reconciliation)

    def test_reconcile_tolerates_a_none_collection(self):
        self.assertIs(
            f.reconcile(None, observation(), env=ON).resolution,  # type: ignore[arg-type]
            f.Resolution.NOTHING_TO_COMPARE,
        )

    def test_horizon_for_never_raises_on_an_unknown_level(self):
        for value in ("", "made_up", None, 7):
            with self.subTest(value=repr(value)):
                self.assertEqual(f.horizon_for(value), 0)

    def test_metadata_for_produces_the_two_declared_keys_and_no_others(self):
        payload = f.metadata_for(observation(subject="  Crypto.Alerts.7.Threshold "))
        self.assertEqual(set(payload), {f.SUBJECT_KEY, f.TRUST_KEY})
        self.assertEqual(payload[f.SUBJECT_KEY], "crypto.alerts.7.threshold")
        self.assertEqual(payload[f.TRUST_KEY], "live_verified")


if __name__ == "__main__":
    unittest.main()
