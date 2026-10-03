"""What one reservation sweep is *permitted* to do (§17).

Until now a single boolean, ``MARKETPLACE_RESERVATION_SWEEPER_DRY_RUN``, stood
between an observing sweep and a mutating one. That one flag conflated four
separate powers:

  A. writing ``expires_at`` onto deadline-less held rows (the legacy backfill)
  B. evaluating which reservations have expired
  C. asking Stripe, with the live key, what a payment intent's status is
  D. actually releasing / capturing / deferring a hold

The owner needs to authorize A without authorizing D. With one flag that is
impossible: flipping it to mutate grants all four at once, against four stranded
production rows dating to 2026-08-13.

Two things make the single flag worse than it looks:

* It never gated C at all. ``_process_candidate`` calls
  ``decide_for_reservation`` *before* its first ``if dry_run``, and the
  reconciler contains no reference to ``dry_run``. So "dry run" already meant
  "no database writes, but yes, live-key Stripe traffic". It reads as a
  promise of no side effects and is not one. The reason production shows
  ``provider_calls=0`` is incidental: the only held rows have no deadline, so
  the candidate query cannot see them, so nothing reaches the provider. Give
  those rows deadlines and a *dry* run starts calling Stripe.
* Coupling A to D means the act of making a row *visible* and the act of
  *releasing* it are one decision, so there is no way to inspect what the
  backfill would expose before exposing it to a release path.

The control model
-----------------

Not four booleans. Four booleans admit sixteen combinations, and the most
dangerous one — mutate without consulting the provider — is exactly what §19
forbids ("age alone is not proof payment failed"). A control surface that can
express a forbidden state eventually reaches it.

Instead: two *ordered* controls, each failing closed, with the dangerous
combination unrepresentable by construction.

``MARKETPLACE_RESERVATION_SWEEP_MODE`` — authority over the normal path:

    observe    read only; no provider calls, no reservation writes
    reconcile  + consult Stripe (plane C); still writes no reservation
    release    + act on the decision (plane D)

Each mode is a strict superset of the one above it, so ``release`` cannot be
selected without ``reconcile``'s provider authority coming with it. Plane D
implies plane C in the type, not in a code review.

``MARKETPLACE_RESERVATION_BACKFILL_MODE`` — authority over plane A, kept
*orthogonal* on purpose:

    off        do not touch deadline-less rows
    dry_run    evaluate and report what would be backfilled; write nothing
    apply      write the deadlines

Orthogonal rather than a fourth rung on the same ladder because backfill acts
on a *different population* (legacy rows no sweep can see) and carries a
different risk. The two directions the owner actually needs are both
expressible, and neither implies the other:

    BACKFILL_MODE=apply  SWEEP_MODE=observe   repair the legacy rows, release
                                              nothing — the §17 requirement
    BACKFILL_MODE=off    SWEEP_MODE=release   run the normal path, leave the
                                              four legacy rows alone

Plane B is not a control. It is a pure read with no side effect, and a sweep
that cannot evaluate candidates cannot report anything either, so disabling it
would only produce a sweep that lies by omission. It is always on.

Migration
---------

Production currently runs ``ENABLED=true`` / ``DRY_RUN=true`` on the
``coinpilotx-pulse-worker`` service. When the new variables are unset the
legacy pair is honoured, and the mapping is chosen to preserve *today's*
behaviour exactly rather than today's naming:

    DRY_RUN truthy/unset  ->  sweep=reconcile, backfill=dry_run
    DRY_RUN falsy         ->  sweep=release,   backfill=apply

``reconcile``, not ``observe``, is the legacy image of a dry run — because as
described above a legacy dry run does call Stripe. Mapping it to ``observe``
would be the tidier story and a silent behaviour change. ``observe`` is
therefore a genuinely new, stricter setting that an operator opts into.

``ENABLED`` is untouched and still decides whether a sweep happens at all; it
lives in the worker's scheduler, not here.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

#: Authority over the normal expiry path, least to most.
SWEEP_MODE_OBSERVE = "observe"
SWEEP_MODE_RECONCILE = "reconcile"
SWEEP_MODE_RELEASE = "release"

#: Ordered. Index is the rung, so comparisons are on the order, not the string.
SWEEP_MODE_LADDER = (SWEEP_MODE_OBSERVE, SWEEP_MODE_RECONCILE, SWEEP_MODE_RELEASE)

#: Authority over the legacy deadline backfill, least to most.
BACKFILL_MODE_OFF = "off"
BACKFILL_MODE_DRY_RUN = "dry_run"
BACKFILL_MODE_APPLY = "apply"

BACKFILL_MODE_LADDER = (BACKFILL_MODE_OFF, BACKFILL_MODE_DRY_RUN, BACKFILL_MODE_APPLY)

SWEEP_MODE_ENV_VAR = "MARKETPLACE_RESERVATION_SWEEP_MODE"
BACKFILL_MODE_ENV_VAR = "MARKETPLACE_RESERVATION_BACKFILL_MODE"

#: The pre-§17 flag, still honoured when the two above are unset.
LEGACY_DRY_RUN_ENV_VAR = "MARKETPLACE_RESERVATION_SWEEPER_DRY_RUN"

#: Accepted spellings of "no". Everything else, including an empty or absent
#: value, is "yes, stay in dry run" — the direction that writes nothing.
_FALSEY = {"0", "false", "no", "off", "n", "f"}

#: How the authority was arrived at. Carried into the sweep summary so an
#: operator reading a log line can tell a deliberate setting from an inherited
#: default, which is the difference between "this is configured" and "nobody
#: has configured this".
SOURCE_EXPLICIT = "explicit"
SOURCE_LEGACY = "legacy"
SOURCE_DEFAULT = "default"


@dataclass(frozen=True)
class ReservationAuthority:
    """The four planes, resolved. Immutable: one sweep, one authority.

    Frozen because the alternative is a sweep that re-reads a flag halfway
    through and releases under one authority what it selected under another.
    The worker builds this once per cycle, which is also what makes a Railway
    variable change take effect on the next sweep without a redeploy.
    """

    sweep_mode: str
    backfill_mode: str
    source: str = SOURCE_DEFAULT

    # --- plane A ---------------------------------------------------------
    @property
    def backfill_evaluates(self) -> bool:
        """Whether deadline-less rows are examined at all."""
        return self.backfill_mode in (BACKFILL_MODE_DRY_RUN, BACKFILL_MODE_APPLY)

    @property
    def backfill_writes(self) -> bool:
        """Whether a deadline is actually written."""
        return self.backfill_mode == BACKFILL_MODE_APPLY

    # --- plane C ---------------------------------------------------------
    @property
    def read_provider(self) -> bool:
        """Whether this sweep may spend a live-key Stripe read."""
        return self._rung >= SWEEP_MODE_LADDER.index(SWEEP_MODE_RECONCILE)

    # --- plane D ---------------------------------------------------------
    @property
    def mutate_reservations(self) -> bool:
        """Whether a hold may actually be released, captured or deferred."""
        return self._rung >= SWEEP_MODE_LADDER.index(SWEEP_MODE_RELEASE)

    @property
    def _rung(self) -> int:
        # ``index`` rather than a dict so an unknown mode raises here instead
        # of silently answering False to every question. Unknown values are
        # normalised away in ``resolve``; reaching this with one is a bug and
        # should be loud.
        return SWEEP_MODE_LADDER.index(self.sweep_mode)

    @property
    def dry_run(self) -> bool:
        """Legacy spelling, for call sites not yet converted.

        Reports whether reservations are left unmutated, which is what every
        existing caller means by it. Deliberately *not* a claim about Stripe
        traffic — that is ``read_provider``, and conflating the two is the bug
        this module exists to undo.
        """
        return not self.mutate_reservations

    def describe(self) -> str:
        """One token for a log line: ``sweep=release/backfill=apply``."""
        return f"sweep={self.sweep_mode}/backfill={self.backfill_mode}"

    def summary(self) -> dict:
        """The authority as data, for the sweep result and the metrics (§51)."""
        return {
            "sweep_mode": self.sweep_mode,
            "backfill_mode": self.backfill_mode,
            "authority_source": self.source,
            "may_backfill": self.backfill_writes,
            "may_read_provider": self.read_provider,
            "may_mutate": self.mutate_reservations,
        }


def _normalise(raw, ladder, *, default):
    """Map an environment string onto a rung, failing closed.

    Anything unrecognised becomes ``default`` — the least authority on the
    ladder — because the realistic typo is in the direction of more power
    (``MODE=releaase``), and a typo must never be read as permission.
    """
    token = (raw or "").strip().lower().replace("-", "_")
    if token in ladder:
        return token
    return default


def resolve(env=None) -> ReservationAuthority:
    """Build the authority for one sweep from the environment.

    Read at call time, never cached, so a Railway variable change lands on the
    next cycle instead of requiring a redeploy — the same contract every other
    configuration reader in this subsystem keeps.
    """
    environ = os.environ if env is None else env

    raw_sweep = (environ.get(SWEEP_MODE_ENV_VAR) or "").strip()
    raw_backfill = (environ.get(BACKFILL_MODE_ENV_VAR) or "").strip()

    if raw_sweep or raw_backfill:
        # At least one new variable is set. Each is resolved on its own, so
        # setting only the backfill control does not silently grant the normal
        # path any authority: an unset SWEEP_MODE is `observe`.
        return ReservationAuthority(
            sweep_mode=_normalise(raw_sweep, SWEEP_MODE_LADDER,
                                  default=SWEEP_MODE_OBSERVE),
            backfill_mode=_normalise(raw_backfill, BACKFILL_MODE_LADDER,
                                     default=BACKFILL_MODE_OFF),
            source=SOURCE_EXPLICIT,
        )

    # Neither new variable is set: honour the flag production is running on.
    raw_legacy = environ.get(LEGACY_DRY_RUN_ENV_VAR)
    if raw_legacy is None:
        # Nothing configured anywhere. Least authority, and say so, so the
        # summary distinguishes "defaulted" from "an operator chose dry run".
        return ReservationAuthority(
            sweep_mode=SWEEP_MODE_RECONCILE,
            backfill_mode=BACKFILL_MODE_DRY_RUN,
            source=SOURCE_DEFAULT,
        )

    legacy_dry_run = (raw_legacy or "").strip().lower() not in _FALSEY
    if legacy_dry_run:
        return ReservationAuthority(
            sweep_mode=SWEEP_MODE_RECONCILE,
            backfill_mode=BACKFILL_MODE_DRY_RUN,
            source=SOURCE_LEGACY,
        )
    return ReservationAuthority(
        sweep_mode=SWEEP_MODE_RELEASE,
        backfill_mode=BACKFILL_MODE_APPLY,
        source=SOURCE_LEGACY,
    )


def from_dry_run(dry_run: bool) -> ReservationAuthority:
    """Adapt a bare ``dry_run`` boolean, for callers and tests not yet moved.

    Mirrors the legacy mapping in ``resolve`` so a test that passes
    ``dry_run=True`` gets the same four planes a legacy-configured worker gets,
    rather than a stricter set that would make the test pass for a reason
    production does not share.
    """
    if dry_run:
        return ReservationAuthority(sweep_mode=SWEEP_MODE_RECONCILE,
                                    backfill_mode=BACKFILL_MODE_DRY_RUN,
                                    source=SOURCE_LEGACY)
    return ReservationAuthority(sweep_mode=SWEEP_MODE_RELEASE,
                                backfill_mode=BACKFILL_MODE_APPLY,
                                source=SOURCE_LEGACY)
