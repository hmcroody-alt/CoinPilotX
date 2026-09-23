"""A malformed experiment must fail at definition, not at the call site.

Every refusal below raises when the definition is constructed. The alternative —
validating at evaluation time — means a bad definition sits in the codebase
looking fine until a request arrives, and then fails on the surface it was
supposed to be improving.
"""

from __future__ import annotations

import pytest

from services.pulse_experiments import registry
from services.pulse_experiments.registry import (
    PROTECTED_DOMAINS,
    ExperimentDefinition,
    ExperimentDefinitionError,
)

from .conftest import definition


@pytest.mark.parametrize("domain", sorted(PROTECTED_DOMAINS))
def test_no_experiment_may_be_defined_in_a_protected_domain(domain):
    """Every protected domain, not a sample — the untested one is the one used."""
    with pytest.raises(ExperimentDefinitionError, match="protected domain"):
        definition(key=f"{domain}.button_copy")


@pytest.mark.parametrize("domain", sorted(PROTECTED_DOMAINS))
def test_a_protected_domain_is_refused_in_any_segment_not_only_the_first(domain):
    """`checkout.payment.retry` is as much a payment experiment as `payment.retry`."""
    with pytest.raises(ExperimentDefinitionError, match="protected domain"):
        definition(key=f"checkout.{domain}.retry")


@pytest.mark.parametrize(
    "key", ["repayment_ui", "reorder_prompt", "authoring.toolbar", "privacy_policy_link"]
)
def test_the_refusal_matches_whole_segments_and_not_substrings(key):
    """A substring rule would refuse innocent keys and teach authors to evade it.

    `repayment_ui` contains "payment" and has nothing to do with money;
    `authoring` contains "auth". If the rule fired on these, the next author
    would rename around it rather than reconsider, and the rule would end up
    protecting nothing while looking strict.
    """
    assert definition(key=key).key == key


@pytest.mark.parametrize(
    "key",
    ["", "   ", "Feed.Density", "feed density", "feed..density", "9lives", "feed-density"],
)
def test_a_key_that_is_unsafe_in_a_log_line_is_refused(key):
    with pytest.raises(ExperimentDefinitionError):
        definition(key=key)


def test_an_over_long_key_is_refused():
    with pytest.raises(ExperimentDefinitionError, match="exceeds"):
        definition(key="a" * (registry.MAX_KEY_LENGTH + 1))


def test_weights_must_sum_to_one_hundred():
    with pytest.raises(ExperimentDefinitionError, match="sum to 100"):
        definition(variants=(("control", 50), ("compact", 30)))


def test_a_control_arm_is_mandatory():
    """Control is what every failure path returns, so it has to be a real arm."""
    with pytest.raises(ExperimentDefinitionError, match="control"):
        definition(variants=(("compact", 50), ("roomy", 50)))


def test_duplicate_variant_names_are_refused():
    with pytest.raises(ExperimentDefinitionError, match="unique"):
        definition(variants=(("control", 50), ("control", 50)))


@pytest.mark.parametrize("rollout", [-1, 101, 1.5, True, "50", None])
def test_a_rollout_outside_zero_to_one_hundred_or_of_the_wrong_type_is_refused(rollout):
    """`True` is `1`, so a bool would silently define a 1% rollout."""
    with pytest.raises(ExperimentDefinitionError):
        definition(rollout_percent=rollout)


@pytest.mark.parametrize("weight", [1.5, True, "50", None, -10])
def test_a_weight_of_the_wrong_type_is_refused(weight):
    with pytest.raises(ExperimentDefinitionError):
        definition(variants=(("control", 100), ("compact", weight)))


def test_a_description_is_required():
    """An experiment nobody described cannot be concluded by anyone but its author."""
    with pytest.raises(ExperimentDefinitionError, match="description"):
        definition(description="  ")


def test_at_least_one_variant_is_required():
    with pytest.raises(ExperimentDefinitionError, match="at least one variant"):
        definition(variants=())


def test_a_definition_cannot_be_mutated_after_construction():
    """Two reads in one request returning different arms would show both sides at once."""
    spec = definition()

    with pytest.raises(Exception):
        spec.rollout_percent = 100  # type: ignore[misc]


def test_two_definitions_sharing_a_key_are_refused_rather_than_merged():
    """Otherwise import order decides which one wins, and it varies by process."""
    with pytest.raises(ExperimentDefinitionError, match="defined twice"):
        registry.build([definition(), definition()])


def test_an_unknown_key_is_none_rather_than_an_error():
    """Deleting a definition ends an experiment; it must not take the page down."""
    built = registry.build([definition()])

    assert built.get("never.defined") is None
    assert built.get("") is None
    assert built.get(None) is None


def test_the_shipped_registry_is_empty_on_purpose():
    """At 41 users no experiment could conclude. See the design record.

    This asserts a deliberate decision rather than an accident, so adding the
    first real definition is a change someone makes knowingly — and has to
    update this test to make.
    """
    assert registry.ACTIVE.keys() == ()


def test_a_valid_definition_survives_all_of_the_above():
    """The suite must be able to build one, or every refusal above is vacuous."""
    spec = ExperimentDefinition(
        key="feed.card_density",
        description="Does a denser feed card change scroll depth?",
        variants=(("control", 50), ("compact", 50)),
        rollout_percent=25,
        enabled=True,
    )

    assert spec.variant_names() == ("control", "compact")
