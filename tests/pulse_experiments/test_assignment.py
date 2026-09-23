"""Assignment must be stable, independent per experiment, and safe when it fails.

The properties here are the ones that make a result trustworthy rather than the
ones that make it work. An assignment function that returns a variant is easy;
one that returns the *same* variant tomorrow, that does not reshuffle when a
rollout widens, and that sends every error to control, is the whole product.
"""

from __future__ import annotations

import pytest

from services.pulse_experiments import assignment, config
from services.pulse_experiments.assignment import CONTROL, assign

from .conftest import definition

SUBJECTS = list(range(1, 2001))


def test_the_same_subject_gets_the_same_arm_every_time(monkeypatch):
    """Determinism, restated across fresh calls — the point of hashing not storing."""
    spec = definition()

    first = assign(spec, 77)
    again = [assign(spec, 77).variant for _ in range(25)]

    assert set(again) == {first.variant}


def test_two_experiments_do_not_assign_the_same_subject_the_same_way(monkeypatch):
    """Carryover is the failure this prevents.

    If the bucket did not mix in the experiment key, a subject unlucky in one
    experiment would be unlucky in every experiment simultaneously. Effects
    would then compound along a hidden axis, and the second experiment would be
    measuring the first.
    """
    a = definition(key="feed.card_density")
    b = definition(key="reels.autoplay_delay")

    disagreements = sum(
        1 for s in SUBJECTS if assign(a, s).variant != assign(b, s).variant
    )

    # Independent 50/50 splits disagree about half the time. Anything near 0
    # would mean the two experiments share a bucket.
    assert 800 < disagreements < 1200, disagreements


def test_widening_a_rollout_does_not_move_anyone_already_inside():
    """The reason eligibility and variant are hashed under separate namespaces.

    With one shared bucket the arm boundaries sit inside the rollout range, so
    raising 10% to 40% slides every boundary and reassigns subjects who were
    already enrolled — quietly invalidating the comparison the widening was
    meant to strengthen.
    """
    narrow = definition(rollout_percent=10)
    wide = definition(rollout_percent=40)

    enrolled = {s: assign(narrow, s) for s in SUBJECTS}
    enrolled = {s: a for s, a in enrolled.items() if a.in_experiment}
    assert enrolled, "the narrow rollout enrolled nobody; the test proves nothing"

    for subject, before in enrolled.items():
        after = assign(wide, subject)
        assert after.in_experiment, subject
        assert after.variant == before.variant, subject


def test_a_partial_rollout_is_still_split_by_the_weights():
    """The sharper symptom of one shared bucket, and the reason there are two.

    With a single hash serving both eligibility and variant, every enrolled
    subject sits in the low end of the bucket space — below the rollout
    ceiling — which is entirely inside the first arm. A 10% rollout of a 50/50
    experiment then puts 100% of its population in control and reports a
    perfectly null result forever, while looking like it is running.

    The widening test above does not catch this: with everyone in control both
    before and after, nobody moves.
    """
    spec = definition(rollout_percent=10, variants=(("control", 50), ("compact", 50)))

    arms = [assign(spec, s).variant for s in SUBJECTS if assign(spec, s).in_experiment]

    assert len(arms) > 100, "too few enrolled to judge the split"
    compact = arms.count("compact")
    assert 0.3 < compact / len(arms) < 0.7, (
        f"{compact}/{len(arms)} in the treatment arm — the enrolled population is "
        "not being split by the weights"
    )


def test_a_wider_rollout_is_a_superset_not_a_resample():
    narrow = {s for s in SUBJECTS if assign(definition(rollout_percent=10), s).in_experiment}
    wide = {s for s in SUBJECTS if assign(definition(rollout_percent=40), s).in_experiment}

    assert narrow < wide


@pytest.mark.parametrize("rollout,low,high", [(0, 0, 0), (10, 150, 250), (100, 2000, 2000)])
def test_the_rollout_percentage_is_actually_honoured(rollout, low, high):
    """0 and 100 exactly; the middle within sampling noise over 2000 subjects."""
    spec = definition(rollout_percent=rollout)

    enrolled = sum(1 for s in SUBJECTS if assign(spec, s).in_experiment)

    assert low <= enrolled <= high, enrolled


def test_the_weights_split_the_enrolled_population_as_declared():
    spec = definition(variants=(("control", 25), ("compact", 75)))

    arms = [assign(spec, s).variant for s in SUBJECTS]

    compact = arms.count("compact")
    assert 1400 < compact < 1600, compact
    assert compact + arms.count(CONTROL) == len(SUBJECTS), "an arm went missing"


def test_a_zero_weight_variant_is_never_assigned():
    """Weight 0 is how an arm is retired without deleting it and reshuffling."""
    spec = definition(variants=(("control", 100), ("compact", 0)))

    assert {assign(spec, s).variant for s in SUBJECTS} == {CONTROL}


@pytest.mark.parametrize("subject", [None, 0, -1, "", "   ", True, False])
def test_an_unusable_subject_is_control_and_is_not_bucketed(subject):
    """Not "bucketed as user 1" — `True` is `1`, and user 1 is the only seller.

    Hashing an empty subject would also put every anonymous caller in one arm
    together, which looks like a cohort in a readout and is really a bug.
    """
    result = assign(definition(), subject)

    assert result.variant == CONTROL
    assert result.in_experiment is False
    assert result.reason == "no_subject"
    assert result.bucket is None


def test_a_disabled_experiment_sends_everyone_to_control():
    spec = definition(enabled=False, rollout_percent=100)

    results = [assign(spec, s) for s in SUBJECTS[:50]]

    assert {r.variant for r in results} == {CONTROL}
    assert {r.reason for r in results} == {"experiment_disabled"}


def test_the_global_kill_switch_overrides_a_fully_enabled_experiment(monkeypatch):
    """The incident control: one variable, every experiment off, no reasoning required."""
    monkeypatch.setenv(config.ENABLED_ENV, "false")
    spec = definition(rollout_percent=100)

    results = [assign(spec, s) for s in SUBJECTS[:50]]

    assert {r.variant for r in results} == {CONTROL}
    assert {r.reason for r in results} == {"globally_disabled"}


def test_the_kill_switch_defaults_to_on_when_unset(monkeypatch):
    """Unset must not mean off, or an unprovisioned environment disables silently."""
    monkeypatch.delenv(config.ENABLED_ENV, raising=False)

    assert config.enabled() is True


def test_a_missing_definition_is_control_rather_than_a_crash():
    """Deleting a definition is how an experiment ends; it must not 500 a page."""
    result = assign(None, 42)

    assert result.variant == CONTROL
    assert result.reason == "no_definition"


def test_the_reason_distinguishes_the_four_ways_to_reach_control():
    """`variant == control` is ambiguous; the readout depends on telling them apart."""
    reasons = {
        assign(None, 1).reason,
        assign(definition(enabled=False), 1).reason,
        assign(definition(), None).reason,
        assign(definition(rollout_percent=0), 1).reason,
    }

    assert reasons == {
        "no_definition",
        "experiment_disabled",
        "no_subject",
        "not_in_rollout",
    }


def test_changing_the_salt_reshuffles_every_arm(monkeypatch):
    """Recorded because it is the cost of deriving rather than storing.

    This is why the salt is a deployment setting and not a form field: flipping
    it mid-experiment swaps arms under people who are already enrolled.
    """
    spec = definition()
    before = [assign(spec, s).variant for s in SUBJECTS[:400]]

    monkeypatch.setenv(config.ASSIGNMENT_SALT_ENV, "a-different-salt")
    after = [assign(spec, s).variant for s in SUBJECTS[:400]]

    moved = sum(1 for b, a in zip(before, after) if b != a)
    assert moved > 100, "the salt is not reaching the bucket"


def test_the_bucket_space_is_fine_enough_for_a_sub_one_percent_rollout():
    """100 buckets would round every small rollout to nothing or everything."""
    assert assignment.BUCKETS >= 10000
