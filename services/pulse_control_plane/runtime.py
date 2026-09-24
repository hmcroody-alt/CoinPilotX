"""Wave 1 — the first module in this package allowed to sit on a request path.

Every other module here describes, measures or refuses. This one answers a
question a live request asked, which makes it the only place where being wrong
costs a user something. The package docstring's closing line — "Nothing here
sits on a request path" — stops being true at this module and nowhere else.

What wave 1 is actually for
---------------------------

Not to change any answer. Ten capabilities were chosen precisely because they
resolve **identically** under both engines, so if production behaviour changes
during this wave, the wiring is the cause and there is no semantic argument to
have about it. Wave 1 proves the request path *can* consult the control plane.
Waves 2 and 3 are where answers change.

That identity is a property to be exploited, not just noted. It means arming
and disarming this module are observationally equivalent for the ten keys, so
a disarm can only restore prior behaviour and can never widen beyond it. Any
divergence is a bug in this module, and :mod:`tests` asserts the equivalence
cell by cell rather than trusting the claim.

Armed and disarmed
------------------

Disarmed is not "deny". Disarmed is **no opinion** — :func:`consult` returns
``None`` and the caller proceeds exactly as it did before wave 1. That is the
rollback the wave plan specifies ("remove the consultation"), available without
a deploy.

Arming requires two independent things to be true:

1. ``PULSE_CONTROL_PLANE_CONSULTATION`` is truthy in the environment. The
   environment plane is the one plane in this codebase that actually gates
   anything, which is why the switch lives there and not in the table this
   package spent two missions establishing is decorative.
2. :func:`arm` verified the live ``feature_flags`` rows against
   :data:`~services.pulse_control_plane.capabilities.CAPABILITIES` at boot and
   found them in agreement.

Both, not either. A variable set against a database that disagrees with the
registry is exactly the state in which a consultation would be confidently
wrong, so it stays disarmed and says why.

Why the switch is not an override
----------------------------------

:func:`~services.pulse_control_plane.model.evaluate` refuses a ``force``
argument, an environment override and an admin bypass, on the grounds that
those are the affordances reached for during an incident that then stay. This
switch is not one of them, and the distinction is worth stating because it
looks like one.

An override changes an answer the plane gave. This decides **whether the
question is asked at all**. When it is off no decision is produced, so there is
no decision for it to have overridden. Nothing in here can turn a deny into an
allow; the only two outcomes are "the plane's answer" and "no answer".

Why this does not read the database per request
------------------------------------------------

It could. It deliberately does not, for two reasons, and the second is the one
that matters.

The cheap reason: the connection pool is 8+8 with a three-second timeout, and a
per-request feature check on ``pulse_posts`` and ``ai_assistant`` — the busiest
two subsystems on the platform — is how a pool gets exhausted by something that
looks like a lookup.

The real reason: a per-request read introduces a third outcome, "the control
plane could not answer", onto a path that previously had two. That outcome has
to be handled somewhere, and every handling of it is a bad option. Treating it
as a deny lets a database blip withdraw the feed. Treating it as an allow is a
fail-open hole of exactly the shape this package spent a mission closing. The
honest fix is to not create the outcome: resolve at boot, hold the answer in
memory, and let a request path that cannot reach the database fail for the
reason it actually failed.

Drift between boot and now is real and is not handled here. It is caught by
``scripts/capability_drift_gate.py`` in CI and by :func:`arm` on the next boot.
A capability whose stored row is edited mid-process keeps answering from the
registry until the process restarts — which is a knowingly accepted window,
recorded here so the next reader does not mistake it for an oversight.

Scope is an allowlist, not a filter
-----------------------------------

A key outside :data:`WAVE_1_KEYS` gets ``None``, the same as disarmed. It does
not fall through to some default and it is not quietly evaluated anyway. The
point of running a cutover in waves is that the scope of each one is written
down; a module that would happily answer for ``marketplace_checkout`` because
the key was passed to it has no waves, only an ordering convention.
"""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from typing import Mapping, Optional

from services.pulse_control_plane import capabilities as _capabilities
from services.pulse_control_plane.model import (
    MODEL_VERSION,
    Capability,
    CapabilityDecision,
    evaluate,
)

#: The environment variable that arms the consultation. Fails **closed to
#: pre-wave behaviour** when unset, which is not the same as failing closed to
#: a deny -- see the module docstring. Declared in ``.env.example`` because
#: ``tests/protection/test_environment_contract.py`` requires every
#: ``os.getenv`` in the tree to be.
CONSULTATION_ENV = "PULSE_CONTROL_PLANE_CONSULTATION"

#: Values the rest of this codebase treats as true. Re-stated from
#: :mod:`~services.pulse_control_plane.classify` rather than imported so that
#: this module's request-path behaviour does not depend on an inventory module
#: staying importable.
_TRUTHY = frozenset({"1", "true", "yes", "on"})

#: The ten capabilities in wave 1.
#:
#: Written out rather than derived from
#: :func:`~services.pulse_control_plane.activation.waves`, which computes wave 1
#: from the shadow matrix. That derivation is right for a *plan* — it cannot
#: drift out of no-op-first order — and wrong for an *allowlist*, because a
#: derived allowlist silently grows the moment a capability's reconciled state
#: changes, and the scope of a live cutover wave is the last thing that should
#: widen without anyone typing it. A test pins this set equal to the derived
#: one, so the two can disagree only in a diff somebody reviewed.
#:
#: Every one is ``LIVE_GLOBAL`` / ``STANDARD`` under the new model and resolves
#: to ``visible=True, usable=True`` for every subject under the legacy engine.
#: The three absentees are absent for reasons, not by oversight:
#:
#: * ``premium_identity`` and ``premium_advanced_tools`` are wave 2, because
#:   wiring them *withdraws* access the legacy row granted by accident;
#: * ``marketplace_checkout`` is wave 3, the only widening in the matrix and
#:   the only row touching payment;
#: * ``pulse_livestream`` and ``admin_command`` are in no wave at all. The
#:   first sits on the Agora -> Mux path under the repository's realtime hard
#:   lock, where adding a runtime consultation is a change to that path
#:   whatever the consultation returns. The second is the admin surface, whose
#:   real gate is ``require_admin_page``; putting a second opinion beside a
#:   working authorisation check invites the two to disagree.
WAVE_1_KEYS: frozenset[str] = frozenset(
    {
        "ai_assistant",
        "creator_cockpit",
        "marketplace_browse",
        "merchant_applications",
        "pulse_comments_reactions",
        "pulse_groups",
        "pulse_messenger",
        "pulse_posts",
        "pulse_reels",
        "pulse_spaces",
    }
)

#: Columns :func:`arm` compares. The legacy ``state`` column is read but never
#: compared for equality against the model, because it is *expected* to
#: disagree -- that disagreement is the finding this package exists to record.
VERIFIED_COLUMNS = ("deployment_state", "eligibility_policy")


@dataclass(frozen=True)
class ArmingResult:
    """Whether the consultation is live, and the reason either way.

    ``armed`` alone is not enough to log. A consultation that is off because
    nobody set the variable and one that is off because production disagrees
    with the registry are the same boolean and very different situations, and
    only one of them is an incident.
    """

    armed: bool
    reason: str
    #: Keys whose stored row disagreed with the registry. Non-empty forces
    #: ``armed=False`` regardless of the environment variable.
    disagreements: tuple[str, ...] = ()

    @property
    def blocked_by_disagreement(self) -> bool:
        return bool(self.disagreements)


class _State:
    """Process-wide arming state, guarded because boot is not single-threaded.

    gunicorn workers each run their own import and their own :func:`arm`, so
    this is per-worker rather than per-deploy. That is the correct granularity:
    a worker that could not verify must not answer, whatever its siblings
    managed to do.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._result: Optional[ArmingResult] = None

    def set(self, result: ArmingResult) -> None:
        with self._lock:
            self._result = result

    def get(self) -> Optional[ArmingResult]:
        with self._lock:
            return self._result

    def reset(self) -> None:
        with self._lock:
            self._result = None


_STATE = _State()


def model_version() -> str:
    """The model version a decision from this module carries.

    A function rather than a re-exported constant so a caller cannot bind it at
    import time and keep reporting a version the decisions no longer have.
    """
    return MODEL_VERSION


def env_permits(environ: Optional[Mapping[str, str]] = None) -> bool:
    """Whether the environment variable permits arming.

    Separate from :func:`arm` so a test can assert that the variable alone is
    not sufficient -- which is the property that stops somebody enabling a
    consultation against a database nobody checked.
    """
    source = environ if environ is not None else os.environ
    return str(source.get(CONSULTATION_ENV, "") or "").strip().lower() in _TRUTHY


def _registry() -> dict[str, Capability]:
    return {cap.key: cap for cap in _capabilities.CAPABILITIES}


def verify(live_rows: Mapping[str, Mapping]) -> tuple[str, ...]:
    """Compare stored rows against the registry for the wave-1 keys only.

    Returns the keys that disagree, in sorted order. An empty tuple means the
    two agree about everything wave 1 will ask about.

    Scoped to the wave on purpose. ``marketplace_checkout`` disagrees with its
    legacy ``state`` by design and always will until wave 3; letting that block
    wave 1 would make the gate unsatisfiable, and an unsatisfiable gate does not
    stop a cutover -- it teaches the next operator that the entry criteria are
    decorative.
    """
    registry = _registry()
    disagreements: list[str] = []
    for key in sorted(WAVE_1_KEYS):
        capability = registry.get(key)
        if capability is None:
            disagreements.append(f"{key}: in wave 1 but not in the capability registry")
            continue
        row = live_rows.get(key)
        if row is None:
            disagreements.append(f"{key}: in wave 1 but no row in feature_flags")
            continue
        stored_state = row.get("deployment_state")
        stored_policy = row.get("eligibility_policy")
        if stored_state != capability.deployment_state:
            disagreements.append(
                f"{key}: stored deployment_state {stored_state!r}, "
                f"registry says {capability.deployment_state!r}"
            )
        if stored_policy != capability.eligibility.key:
            disagreements.append(
                f"{key}: stored eligibility_policy {stored_policy!r}, "
                f"registry says {capability.eligibility.key!r}"
            )
    return tuple(disagreements)


def arm(
    live_rows: Optional[Mapping[str, Mapping]] = None,
    *,
    environ: Optional[Mapping[str, str]] = None,
) -> ArmingResult:
    """Decide once, at boot, whether this process may consult the control plane.

    ``live_rows`` is the migrated ``feature_flags`` content, keyed by
    ``feature_key``. Passing ``None`` means the caller could not read the table,
    which disarms — not because an unreadable table is evidence of a problem
    with the rows, but because it is the absence of the evidence arming
    requires.
    """
    if not env_permits(environ):
        result = ArmingResult(
            armed=False,
            reason=f"{CONSULTATION_ENV} is not set to a truthy value; consultation is off",
        )
        _STATE.set(result)
        return result

    if live_rows is None:
        result = ArmingResult(
            armed=False,
            reason=(
                f"{CONSULTATION_ENV} permits consultation but feature_flags could not be "
                "read, so the stored rows were never verified against the registry"
            ),
        )
        _STATE.set(result)
        return result

    disagreements = verify(live_rows)
    if disagreements:
        result = ArmingResult(
            armed=False,
            reason=(
                f"{len(disagreements)} wave-1 row(s) disagree with the capability "
                "registry; refusing to consult a table nobody reconciled"
            ),
            disagreements=disagreements,
        )
        _STATE.set(result)
        return result

    result = ArmingResult(
        armed=True,
        reason=f"{len(WAVE_1_KEYS)} wave-1 row(s) verified against the registry",
    )
    _STATE.set(result)
    return result


def arming() -> Optional[ArmingResult]:
    """The current arming state, or ``None`` if :func:`arm` was never called.

    ``None`` is deliberately distinct from a disarmed result. A process that
    never armed has not decided anything; one that armed and refused has.
    """
    return _STATE.get()


def disarm(reason: str = "disarmed explicitly") -> ArmingResult:
    """Turn the consultation off without a deploy.

    Exists so an incident response does not require a code revert. It cannot
    change an answer, only stop one being produced, so it is not the override
    :func:`~services.pulse_control_plane.model.evaluate` refuses.
    """
    result = ArmingResult(armed=False, reason=reason)
    _STATE.set(result)
    return result


def reset_for_tests() -> None:
    """Clear arming state. Named so its one legitimate caller is obvious."""
    _STATE.reset()


def consult(
    capability_key: str,
    *,
    subject_id: Optional[str] = None,
) -> Optional[CapabilityDecision]:
    """Ask the control plane about one capability, or return ``None``.

    ``None`` means **no opinion** and the caller must proceed exactly as it did
    before wave 1. It is returned when the process is disarmed, when the key is
    outside the wave, and when the key is not in the registry at all. It is
    never returned as a way of expressing denial; a denial is a
    :class:`~services.pulse_control_plane.model.CapabilityDecision` with
    ``usable=False`` and a reason code saying why.

    Wave 1 passes no ``eligibility`` verdict, and must not. All ten capabilities
    carry the ``STANDARD`` policy, which requires no authority, so there is no
    authority to consult. Supplying a fabricated verdict would make this module
    the authority — which is the one thing every module in this package is
    written not to become.
    """
    state = _STATE.get()
    if state is None or not state.armed:
        return None
    if capability_key not in WAVE_1_KEYS:
        return None
    capability = _registry().get(capability_key)
    if capability is None:
        return None
    return evaluate(capability, subject_id=subject_id, source="wave_1")


def permits(capability_key: str, *, subject_id: Optional[str] = None) -> bool:
    """The request-path helper: may this subject use this capability?

    Returns ``True`` when the plane has no opinion, because "no opinion" means
    "behave as you did before wave 1" and before wave 1 every one of these ten
    capabilities was unconditionally available. That is a fact about *this*
    wave's contents and not a general policy, which is why the allowlist in
    :data:`WAVE_1_KEYS` is what bounds it. Reusing this helper for wave 2 or 3
    without revisiting this line would convert a withdrawal into a no-op and a
    grant into a silent widening.
    """
    decision = consult(capability_key, subject_id=subject_id)
    if decision is None:
        return True
    return decision.usable
