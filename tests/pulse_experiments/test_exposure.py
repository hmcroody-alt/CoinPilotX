"""Exposure is a log line, and it must carry the shape without the person.

The two things that make the line worth writing: it distinguishes the ways a
subject reached control, and it counts each subject once per experiment rather
than once per template lookup.
"""

from __future__ import annotations

from services.pulse_experiments import assignment_for, exposure, registry, variant_for
from services.pulse_experiments.assignment import assign

from .conftest import definition


def _lines(caplog):
    return [r.getMessage() for r in caplog.records if "PULSE_EXPERIMENT_EXPOSURE" in r.getMessage()]


def test_an_exposure_is_logged_once_per_subject_per_experiment(caplog):
    """Three lookups while rendering one page is one exposure, not three.

    Otherwise an arm's denominator measures how many times the template asked,
    not how many people saw it.
    """
    spec = definition()
    active = registry.build([spec])

    with caplog.at_level("INFO", logger="services.pulse_experiments.exposure"):
        for _ in range(3):
            variant_for(spec.key, 42, active=active)

    assert len(_lines(caplog)) == 1


def test_two_subjects_are_two_exposures(caplog):
    spec = definition()
    active = registry.build([spec])

    with caplog.at_level("INFO", logger="services.pulse_experiments.exposure"):
        variant_for(spec.key, 42, active=active)
        variant_for(spec.key, 43, active=active)

    assert len(_lines(caplog)) == 2


def test_the_same_subject_in_two_experiments_is_two_exposures(caplog):
    a = definition(key="feed.card_density")
    b = definition(key="reels.autoplay_delay")
    active = registry.build([a, b])

    with caplog.at_level("INFO", logger="services.pulse_experiments.exposure"):
        variant_for(a.key, 42, active=active)
        variant_for(b.key, 42, active=active)

    assert len(_lines(caplog)) == 2


def test_the_line_carries_no_raw_user_id(caplog):
    """A stable token, never the id. The same reasoning as `subject_ref`."""
    spec = definition()
    active = registry.build([spec])

    with caplog.at_level("INFO", logger="services.pulse_experiments.exposure"):
        variant_for(spec.key, 987654321, active=active)

    line = _lines(caplog)[0]
    assert "987654321" not in line
    assert "subject=" in line


def test_the_subject_token_is_stable_across_calls():
    """Unstable would make every exposure a new person and every count wrong."""
    assert exposure.subject_token(42) == exposure.subject_token(42)
    assert exposure.subject_token(42) != exposure.subject_token(43)


def test_the_token_changes_with_the_salt(monkeypatch):
    """Proves the salt reaches the hash rather than being decorative."""
    from services.pulse_experiments import config

    before = exposure.subject_token(42)
    monkeypatch.setenv(config.ASSIGNMENT_SALT_ENV, "another-salt")

    assert exposure.subject_token(42) != before


def test_the_reason_is_on_the_line_so_control_is_not_ambiguous(caplog):
    """`variant=control` alone cannot distinguish a run experiment from a dead one."""
    spec = definition(enabled=False)
    active = registry.build([spec])

    with caplog.at_level("INFO", logger="services.pulse_experiments.exposure"):
        variant_for(spec.key, 42, active=active)

    assert "reason=experiment_disabled" in _lines(caplog)[0]
    assert "in_experiment=0" in _lines(caplog)[0]


def test_an_unknown_experiment_is_logged_under_the_name_that_was_asked_for(caplog):
    """A typo at a call site should be visible, not silent."""
    with caplog.at_level("INFO", logger="services.pulse_experiments.exposure"):
        result = assignment_for("feed.typoed_key", 42, active=registry.build([]))

    assert result.reason == "unknown_experiment"
    assert "experiment=feed.typoed_key" in _lines(caplog)[0]


def test_logging_can_be_declined_by_a_caller_that_is_only_inspecting(caplog):
    spec = definition()
    active = registry.build([spec])

    with caplog.at_level("INFO", logger="services.pulse_experiments.exposure"):
        variant_for(spec.key, 42, active=active, log=False)

    assert _lines(caplog) == []


def test_a_repeat_is_forced_only_when_asked_for(caplog):
    spec = definition()

    with caplog.at_level("INFO", logger="services.pulse_experiments.exposure"):
        result = assign(spec, 42)
        exposure.record(result, 42)
        exposure.record(result, 42)
        exposure.record(result, 42, repeat=True)

    assert len(_lines(caplog)) == 2


def test_an_assignment_with_no_experiment_writes_nothing(caplog):
    with caplog.at_level("INFO", logger="services.pulse_experiments.exposure"):
        wrote = exposure.record(assign(None, 42), 42)

    assert wrote is False
    assert _lines(caplog) == []
