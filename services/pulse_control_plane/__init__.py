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
"""

from __future__ import annotations

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
    "ActivationBlocked",
    "CLASSIFICATIONS",
    "Classification",
    "LEGACY_ROWS",
    "LegacyRow",
    "activation_gate",
    "classify_env_gate",
    "unreconciled",
]
