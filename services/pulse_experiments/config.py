"""Settings for PulseExperiments. One salt, one switch, both documented.

``tests/protection/test_environment_contract.py`` requires every ``os.getenv``
in production code to appear in ``.env.example``, because an undocumented
variable is a silent feature-off switch on a platform that registers its route
packs inside ``except`` blocks. Both names below are declared there.
"""

from __future__ import annotations

import os

ASSIGNMENT_SALT_ENV = "PULSE_EXPERIMENTS_ASSIGNMENT_SALT"
ENABLED_ENV = "PULSE_EXPERIMENTS_ENABLED"

_DEFAULT_ASSIGNMENT_SALT = "pulsesoc-experiments-assignment-v1"

_TRUE = {"1", "true", "on", "yes"}
_FALSE = {"0", "false", "off", "no"}


def assignment_salt() -> str:
    """The salt mixed into every bucket hash.

    Deliberately **not** ``commerce_discovery``'s subject salt. That module's
    own docstring explains why its salt differs from advertising's: a shared
    salt lets anyone holding both tables join a viewer across classes without
    either table naming a person. The same reasoning applies one layer out — if
    experiment assignment were derived from the commerce salt, an exported
    assignment could be joined straight onto the behavioural profile that salt
    exists to protect.

    Changing this value reshuffles every assignment at once. That is a
    destructive act mid-experiment — every subject may swap arms, so the
    before and after cannot be compared — and it is why this is a deployment
    setting rather than something editable from a page.
    """
    return os.environ.get(ASSIGNMENT_SALT_ENV) or _DEFAULT_ASSIGNMENT_SALT


def enabled() -> bool:
    """The global kill switch. Unset means enabled.

    Off sends **every** subject to control, which is the pre-experiment
    behaviour of the product. So the switch cannot itself change what a user
    sees beyond returning them to the default path, and an operator who trips
    it in an incident does not have to reason about which experiments were
    running.
    """
    raw = str(os.environ.get(ENABLED_ENV) or "").strip().lower()
    if raw in _FALSE:
        return False
    return True
