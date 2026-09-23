"""PulseExperiments — deterministic assignment, no store, no causal claim.

    from services.pulse_experiments import variant_for
    if variant_for("feed.card_density", user_id) == "compact":
        ...

One call, and the failure modes all land on ``control``: an unknown key, an
anonymous subject, a disabled experiment or the global kill switch each return
the behaviour the product had before any experiment existed.

This package does not decide anything about payments, authentication,
authorisation, orders, payouts, privacy or fraud, and ``registry`` refuses to
define an experiment in those namespaces at all. See that module for how far
the refusal reaches and where the test does the rest of the work.
"""

from __future__ import annotations

from typing import Any, Optional

from services.pulse_experiments import assignment as _assignment
from services.pulse_experiments import config, exposure, registry
from services.pulse_experiments.assignment import CONTROL, Assignment, assign
from services.pulse_experiments.registry import (
    ACTIVE,
    ExperimentDefinition,
    ExperimentDefinitionError,
    Registry,
    build,
)

__all__ = [
    "ACTIVE",
    "CONTROL",
    "Assignment",
    "ExperimentDefinition",
    "ExperimentDefinitionError",
    "Registry",
    "assign",
    "assignment_for",
    "build",
    "config",
    "exposure",
    "registry",
    "variant_for",
]


def assignment_for(
    key: str,
    subject: Any,
    *,
    active: Optional[Registry] = None,
    log: bool = True,
) -> Assignment:
    """The full assignment for one subject, exposure logged by default.

    Logging is on by default because an arm nobody recorded cannot be counted
    later, and the caller who forgets the second call is the common case.
    """
    active = ACTIVE if active is None else active
    result = _assignment.assign(active.get(key), subject)
    if not result.experiment_key:
        # An unknown key. Reported under the requested name so a typo in a call
        # site is visible in the log rather than appearing as silence.
        result = Assignment(str(key or ""), CONTROL, False, "unknown_experiment")
    if log:
        exposure.record(result, subject)
    return result


def variant_for(
    key: str,
    subject: Any,
    *,
    active: Optional[Registry] = None,
    log: bool = True,
) -> str:
    """Just the arm name, for the overwhelmingly common branch-on-a-string case."""
    return assignment_for(key, subject, active=active, log=log).variant
