"""Fixtures for the PulseExperiments suite.

The salt is pinned rather than left to the environment. Every bucket in these
tests is a function of it, so a developer with
``PULSE_EXPERIMENTS_ASSIGNMENT_SALT`` set in their shell would otherwise see
different arms from CI and chase a failure that is really a local setting.
"""

from __future__ import annotations

import pytest

from services.pulse_experiments import config, exposure, registry

#: Any fixed value. Pinned so the distribution assertions below are reproducible.
TEST_SALT = "test-salt-v1"


@pytest.fixture(autouse=True)
def pinned_environment(monkeypatch):
    monkeypatch.setenv(config.ASSIGNMENT_SALT_ENV, TEST_SALT)
    monkeypatch.delenv(config.ENABLED_ENV, raising=False)
    exposure.reset()
    yield
    exposure.reset()


def definition(
    key="feed.card_density",
    *,
    variants=(("control", 50), ("compact", 50)),
    rollout_percent=100,
    enabled=True,
    description="Does a denser feed card change scroll depth?",
):
    return registry.ExperimentDefinition(
        key=key,
        description=description,
        variants=variants,
        rollout_percent=rollout_percent,
        enabled=enabled,
    )
