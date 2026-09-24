"""PulseControlPlane — a description of what actually controls production.

This package **controls nothing**. That is deliberate and it is the whole point.

PulseSoc has three things that look like a feature-flag system, and the one with
a user interface is the only one that gates nothing at all:

===================  ==================================  ==========  ===========
plane                storage                             has a UI?   gates?
===================  ==================================  ==========  ===========
environment          Railway service variables           no          **yes**
``feature_flags``    Postgres, 15 rows, owner-editable   yes         no
premium flags        Postgres, 7 rows                    read-only   no
===================  ==================================  ==========  ===========

The mission this package answers is "make feature/runtime control describe
production reality *before* it is allowed to control production". So the order
of work is fixed: describe, reconcile, and only then wire. Wiring first is the
specific accident this package exists to prevent, because the seeded values are
not merely stale — six of the fifteen understate what is already shipped, and
``marketplace_checkout`` is seeded ``internal-only`` while production has taken
32 real orders through it. Handing ``evaluate_flag`` authority over that row,
as written, removes checkout from every non-admin.

See :mod:`services.pulse_control_plane.legacy` for the row-by-row reconciliation
and the evidence behind each verdict, and
``docs/control-plane/CONTROL_PLANE_RECONSTRUCTION.md`` for the design record.
Layout
------

Mission 1 described the planes. Mission 2 built the replacement model beside
them and measured the gap, still without wiring anything:

``classify`` / ``legacy`` / ``reconcile``
    Mission 1. What the three planes are, what each of the fifteen rows
    claims, and the gate that refuses activation while any row is unreconciled.

``model`` / ``capabilities`` / ``parsing``
    The two-axis replacement. ``DeploymentState`` is a fact about the system;
    ``EligibilityPolicy`` is a policy that *names* an authority and never
    becomes one. Parsing is strict, because an ambiguous word must not widen
    access — which is precisely what the legacy engine does with one.

``observations`` / ``reconciler`` / ``drift``
    What production actually admits, what the table claims, and the difference
    between them — split into static contract drift, which blocks a build, and
    production-observed drift, which is reported and does not.

``rollout``
    The percentage column, implemented rather than deleted.

``migration``
    The additive, compare-and-set repair. It writes new columns and never
    touches the legacy ``state`` column; ``activation`` explains why leaving
    that column wrong is safer than correcting it.

``shadow`` / ``activation`` / ``write_security``
    The cutover. Both engines' answers for all 75 subject-capability cells,
    the wave order with the gate that must pass before wave 1, and what guards
    the admin write once it starts meaning something.

Nothing here sits on a request path. ``activation.readiness()`` is what says
whether that may change.
"""

from __future__ import annotations

from services.pulse_control_plane import (
    activation,
    capabilities,
    drift,
    env_gates,
    migration,
    model,
    observations,
    parsing,
    reconciler,
    rollout,
    shadow,
    write_security,
)
from services.pulse_control_plane.classify import (
    CLASSIFICATIONS,
    Classification,
    classify_env_gate,
)
from services.pulse_control_plane.legacy import (
    LEGACY_ROWS,
    LegacyRow,
    unreconciled,
)
from services.pulse_control_plane.reconcile import (
    ActivationBlocked,
    activation_gate,
)

__all__ = [
    # Mission 1 — the description.
    "ActivationBlocked",
    "CLASSIFICATIONS",
    "Classification",
    "LEGACY_ROWS",
    "LegacyRow",
    "activation_gate",
    "classify_env_gate",
    "unreconciled",
    # Mission 2 — exported as modules rather than flattened into symbols. Each
    # of these carries its reasoning in a module docstring, and hoisting the
    # functions out would separate every one of them from the argument for why
    # it behaves the way it does. That argument is most of the value here.
    "activation",
    "capabilities",
    "drift",
    "env_gates",
    "migration",
    "model",
    "observations",
    "parsing",
    "reconciler",
    "rollout",
    "shadow",
    "write_security",
]
