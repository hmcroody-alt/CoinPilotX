"""The activation gate: the thing that says "not yet", in code.

The mission's ordering constraint is that no shipped feature may be placed under
``evaluate_flag`` or PulseExperiments until every legacy row is reconciled. A
constraint that lives only in a document is one careless pull request away from
being forgotten, so it is expressed here as a function that raises.

:func:`activation_gate` is intended to be called by whatever eventually wires the
control plane up — not by request paths. It is a build-time and test-time
assertion about the state of the inventory, not a runtime feature check, and it
deliberately has no cache, no environment override and no ``force`` argument.
An override parameter would be the first thing reached for during an incident
and the gate would then protect nothing.

Why the gate stays shut even though every row is now reconciled
---------------------------------------------------------------

Reconciliation was necessary but is not sufficient. ``marketplace_checkout``
carries ``regression_if_wired``: its seeded ``internal-only`` state maps, through
``evaluate_flag``, to invisible-and-unusable for every non-admin, while
production has taken 32 real orders through it. Knowing that is what makes it
unsafe to proceed, not what makes it safe.

So the gate has two conditions and they fail for different reasons: an
unreconciled row means *we do not know*, and a regression row means *we know, and
the answer is no*. Collapsing them into one boolean would lose the distinction
that tells an operator whether the next step is investigation or repair.

Which gate to call
------------------

This one is Mission 1's and remains correct, but it is now the narrower of two.
:func:`services.pulse_control_plane.activation.readiness` is what a cutover
should consult: it subsumes both conditions here and adds the ones Mission 2
found were also necessary — that every widening in the shadow matrix has been
written down individually with its evidence, that no capability on the
never-wire list is scheduled, that the control plane has not begun inferring
authentication, and that ``evaluate_flag`` still has no call sites.

That last condition is what keeps this gate honest. Mission 2's migration
writes the reconciled truth into new columns and deliberately never touches
legacy ``state``, so the seeded words this gate reasons about are still sitting
in the table — ``marketplace_checkout = 'internal-only'`` among them. They are
inert, but only because nothing reads them. The day something does, the
``regression_if_wired`` condition below stops describing a hypothetical and
starts describing production.
"""

from __future__ import annotations

from services.pulse_control_plane.legacy import (
    LEGACY_ROWS,
    regressions_if_wired,
    unreconciled,
)


class ActivationBlocked(RuntimeError):
    """Raised when the control plane is asked to govern before it describes.

    A ``RuntimeError`` rather than a ``ValueError`` because nothing about the
    caller's arguments is wrong — the world is not ready, and the caller cannot
    fix that by passing something different.
    """


def activation_gate() -> None:
    """Permit wiring, or refuse with the specific reason.

    Returns ``None`` when it is safe for ``evaluate_flag`` or PulseExperiments to
    take authority over an existing shipped feature. Raises otherwise.
    """
    pending = unreconciled()
    if pending:
        keys = ", ".join(row.feature_key for row in pending)
        raise ActivationBlocked(
            f"{len(pending)} of {len(LEGACY_ROWS)} legacy feature_flags rows are "
            f"still UNKNOWN: {keys}. Production reality must be established "
            "before the control plane may govern."
        )

    regressions = regressions_if_wired()
    if regressions:
        detail = "; ".join(
            f"{row.feature_key} (seeded {row.seeded_state!r}, actually {row.verdict})"
            for row in regressions
        )
        raise ActivationBlocked(
            f"{len(regressions)} reconciled row(s) would change user-visible "
            f"behaviour if evaluate_flag were given authority: {detail}. "
            "Repair the stored state to match production before wiring."
        )


def summary() -> dict:
    """A machine-readable snapshot of the reconciliation, for the audit script."""
    counts: dict[str, int] = {}
    for row in LEGACY_ROWS:
        counts[row.verdict] = counts.get(row.verdict, 0) + 1
    try:
        activation_gate()
        blocked_reason = ""
    except ActivationBlocked as exc:
        blocked_reason = str(exc)
    return {
        "total_rows": len(LEGACY_ROWS),
        "verdicts": counts,
        "unreconciled": [row.feature_key for row in unreconciled()],
        "regressions_if_wired": [row.feature_key for row in regressions_if_wired()],
        "seed_understates": [r.feature_key for r in LEGACY_ROWS if "seed_understates" in r.tags],
        "seed_overstates": [r.feature_key for r in LEGACY_ROWS if "seed_overstates" in r.tags],
        "activation_allowed": not blocked_reason,
        "blocked_reason": blocked_reason,
    }
