"""Stages 21 + 23 — how the control plane may be switched on, and when.

This module authorises nothing. It computes whether activation is permitted
right now (it is not) and, if it were, in what order. Like everything else here
it is read-only and is not reachable from any request path.

The first wave must change nobody's access
-------------------------------------------

The obvious cutover order is "most valuable first", which here would mean
starting with ``marketplace_checkout`` — the row that has been lying about a
live payment path since May. That order is wrong, and the reason is diagnostic
rather than aesthetic.

Wiring a capability into production access is two changes at once: the *plumbing*
(the request path now consults ``feature_flags`` at all) and the *semantics* (the
value it finds is different from what the old engine would have said). If the
first capability wired is one whose shadow diff is non-empty and something
breaks, there is no way to tell which of the two broke it. If the first wave is
capabilities whose shadow diff is empty, any breakage is unambiguously the
plumbing, and the plumbing is the part with no rollback story of its own.

Eleven of the fifteen capabilities move nobody
(:mod:`~services.pulse_control_plane.shadow`). That is a free control group and
it would be wasteful not to use it.

Two capabilities that must never be wired
------------------------------------------

``admin_command`` governs ``/admin/*``. ``/admin/capability-matrix`` is in
``/admin/*``. A capability row that wrongly denies admins would therefore remove
the only surface from which the row could be corrected — and, because
``init_db()`` no longer re-asserts the stored value, nothing would restore it
either. The recovery path would be a direct Postgres session against production.

That is not a hypothetical shape. It is the ordinary failure of every system
that is allowed to govern its own administration, and the mitigation is not a
better value, it is refusing the wiring. ``require_admin_page`` already enforces
this capability correctly on all 199 routes; the control plane row for it is
documentation and must stay documentation.

``pulse_livestream`` is refused for the different reason given in
``docs/realtime_audio_change_policy.md``: the Agora to Mux path is under a change
hard-lock, and introducing a new runtime consultation into it is a change to it
regardless of what the consultation returns.

On the one widening
-------------------

The shadow matrix contains exactly three widening cells, all
``marketplace_checkout``. They are listed in :data:`ACCEPTED_WIDENINGS` with the
evidence, because an access grant that nobody wrote down is an access grant
nobody reviewed. A widening that is not in that mapping blocks activation
outright — including a future one that happens to be correct. Correct and
reviewed are different properties and this module only checks for the second.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from services.pulse_control_plane import shadow

# ---------------------------------------------------------------------------
# Capabilities that may not be wired at all
# ---------------------------------------------------------------------------

#: key -> why the control plane must not govern it. Not a "later" list.
NEVER_WIRE: dict[str, str] = {
    "admin_command": (
        "Governs /admin/*, which contains /admin/capability-matrix. A row that "
        "wrongly denied admins would delete the surface needed to fix the row, and "
        "since init_db() no longer re-asserts stored values nothing would restore "
        "it — recovery would be a direct production Postgres session. "
        "require_admin_page already enforces this correctly on all 199 routes."
    ),
    "pulse_livestream": (
        "The Agora -> Mux realtime path is under the change hard-lock in "
        "docs/realtime_audio_change_policy.md. Adding a runtime consultation to it "
        "is a change to it whatever the consultation returns."
    ),
}


# ---------------------------------------------------------------------------
# Widenings that have been looked at
# ---------------------------------------------------------------------------

#: (capability_key, subject_key) -> the evidence that makes the grant correct.
#: Deliberately keyed per cell rather than per capability: "checkout widens" is
#: a summary, and a summary is what lets a fourth cell appear unnoticed.
ACCEPTED_WIDENINGS: dict[tuple[str, str], str] = {
    ("marketplace_checkout", "anonymous"): (
        "Not an authentication decision. The control plane does not gate "
        "authentication for any capability (shadow.authentication_gated_cells is "
        "empty); require_account() on /api/business-os/orders does, and probed 401."
    ),
    ("marketplace_checkout", "member"): (
        "32 rows in seller_transactions are completed orders placed by ordinary "
        "members. The stored 'internal-only' does not describe production and has "
        "not since before the first of those orders."
    ),
    ("marketplace_checkout", "premium"): (
        "Same evidence as member; checkout has never been entitlement-gated. "
        "MARKETPLACE_CARD_PAYMENTS_ENABLED=true and BUSINESS_OS_ORDERS=true."
    ),
}


# ---------------------------------------------------------------------------
# Waves
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Wave:
    """One increment of the cutover, with what must hold before it starts."""

    number: int
    name: str
    capability_keys: tuple[str, ...]
    purpose: str
    entry_criteria: tuple[str, ...]
    rollback: str

    def __post_init__(self) -> None:
        forbidden = sorted(set(self.capability_keys) & set(NEVER_WIRE))
        if forbidden:
            raise ValueError(
                f"wave {self.number} ({self.name}) schedules capabilities that must never "
                f"be wired: {forbidden}. See NEVER_WIRE for why each is refused."
            )
        if not self.rollback:
            raise ValueError(
                f"wave {self.number} has no rollback. A cutover step you cannot undo is "
                "not a wave, it is a launch."
            )


def _no_op_capabilities() -> tuple[str, ...]:
    """Capabilities whose shadow diff is empty, minus anything never-wired."""
    cells = shadow.compare_all()
    moving = {c.capability_key for c in cells if c.changes_behaviour}
    return tuple(
        sorted(
            {c.capability_key for c in cells} - moving - set(NEVER_WIRE)
        )
    )


def waves() -> tuple[Wave, ...]:
    """The cutover order, derived from the shadow matrix rather than declared.

    Deriving it matters: a hand-written wave list would silently stop being the
    no-op-first order the moment a capability's reconciled state changed, and it
    would still look correct.
    """
    return (
        Wave(
            number=1,
            name="Plumbing, no semantic change",
            capability_keys=_no_op_capabilities(),
            purpose=(
                "Prove the request path can consult the control plane at all. Every "
                "capability here resolves identically under both engines, so any "
                "change in production behaviour is the wiring and nothing else."
            ),
            entry_criteria=(
                "Stage 11 migration applied; every row reconciles MATCH.",
                # Deliberately not "--strict exits 0". That gate measures the
                # legacy ``state`` word, which the migration is forbidden to
                # repair, so it reports four findings permanently and no action
                # can clear them. Naming it here would have made wave 1
                # unreachable and taught the next operator that the entry
                # criteria are decorative. The activation check is the one that
                # reads the migrated columns and can therefore actually go green.
                "capability_activation_check.py --database-url <prod> exits 0.",
                "Shadow diff for these keys is empty.",
            ),
            rollback="Remove the consultation. No stored value changes, so there is nothing to restore.",
        ),
        Wave(
            number=2,
            name="Withdrawals",
            capability_keys=("premium_identity", "premium_advanced_tools"),
            purpose=(
                "Both are stored as words meaning 'everyone' while the premium "
                "engines gate them per subject. Wiring them withdraws access the "
                "legacy row granted by accident. Denied subjects still see the "
                "feature — that is the upsell, and losing it would be the "
                "regression to watch for here."
            ),
            entry_criteria=(
                "Wave 1 stable in production for one full release cycle.",
                "services.business_os.entitlements.premium confirmed as the single "
                "resolver; BUSINESS_OS_ENTITLEMENTS=canonical.",
                "Owner-level accounts confirmed to expect denial: the legacy engine "
                "treated an owner as premium, and the entitlement service does not.",
            ),
            rollback="Remove the consultation for these two keys; access returns immediately.",
        ),
        Wave(
            number=3,
            name="The grant",
            capability_keys=("marketplace_checkout",),
            purpose=(
                "The only widening in the matrix, and the only row touching PAYMENT "
                "and ORDER_INTEGRITY. Last because it is the one wave whose failure "
                "mode is a customer who cannot complete a purchase."
            ),
            entry_criteria=(
                "Waves 1 and 2 stable.",
                "All three widening cells present in ACCEPTED_WIDENINGS.",
                "Capability is protected, so rollout_percentage must be 100 — a "
                "partial payment rollout is an outage for the remainder.",
                "Checkout verified on device, not only by static check.",
            ),
            rollback=(
                "Remove the consultation. Note the asymmetry: rollback restores the "
                "legacy row's answer, which is 'internal-only' and would hide "
                "checkout from every non-admin. Rolling this wave back therefore "
                "requires removing the consultation, NOT reverting the stored value."
            ),
        ),
    )


# ---------------------------------------------------------------------------
# Stage 23 — readiness
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ActivationReadiness:
    """Whether the control plane may be wired into production access today."""

    ready: bool
    blockers: tuple[str, ...] = field(default_factory=tuple)
    notes: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if self.ready and self.blockers:
            raise ValueError("ready with blockers is not a state")


def legacy_engine_call_sites() -> tuple[str, ...]:
    """Files on the request path that call ``evaluate_flag``.

    Expected to be empty, and it is the precondition that makes everything else
    here coherent. See :func:`readiness` for why.

    The ``def`` line is excluded, which is not a nicety. A substring scan for
    ``evaluate_flag(`` matches the function's own definition, so the first
    version of this reported ``services/feature_flag_engine.py`` as a call site
    and declared the legacy engine live on a tree where it is called by nothing.
    That is the third time this package has had to draw the same distinction —
    an auditor naming a variable is not a reader of it, a catalog describing a
    gate is not a gate, and a definition is not a call site. A scan that cannot
    tell mention from use will always find the thing that talks about the thing.

    ``services/pulse_control_plane/`` is outside the scanned roots, which is why
    :mod:`~services.pulse_control_plane.shadow` calling ``evaluate_flag`` does
    not register. That is correct and is carried by a separate guarantee:
    nothing on the request path imports ``shadow`` either.
    """
    from services.pulse_control_plane import drift

    found = []
    for path, text in drift._searchable_sources():
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith(("def ", "async def ", "#")):
                continue
            if "evaluate_flag(" in stripped:
                found.append(path)
                break
    return tuple(found)


def _reconciliations(signals: Optional[dict], stored: Optional[dict]):
    """Reconcile against the same inputs the drift detector used.

    Recomputed here rather than carried on :class:`~drift.GateResult`, which
    holds findings and should keep holding only findings: a gate result that
    also carried its intermediate working would invite callers to re-derive
    conclusions from it and disagree with the gate.
    """
    from services.pulse_control_plane.observations import (
        PRODUCTION_SIGNALS,
        STORED_STATES,
    )
    from services.pulse_control_plane.reconciler import reconcile_all

    return reconcile_all(
        PRODUCTION_SIGNALS if signals is None else signals,
        STORED_STATES if stored is None else stored,
    )


def readiness(
    *,
    signals: Optional[dict] = None,
    stored: Optional[dict] = None,
    migrated: Optional[dict[str, str]] = None,
    today: Optional[object] = None,
) -> ActivationReadiness:
    """Compose every precondition. Fails closed, and is expected to fail today.

    Stage 23 requires zero drift before ``feature_flags`` may be wired into
    production access. "Zero drift" is checked against the *observed* half as
    well as the static half — the opposite of what CI does, and deliberately so.
    CI must not fail a stranger's pull request because a production row is
    stale; activation must not proceed while one is.

    Why ``migrated`` has to exist for this gate to be passable
    ---------------------------------------------------------

    The Stage 11 migration writes the reconciled truth into new columns and
    deliberately never touches ``feature_flags.state``. So after it runs,
    ``state`` still reads ``internal-only`` for a capability that has taken
    thirty-two orders — and a zero-drift gate measured against that column could
    never go green, no matter what anybody did. A gate that cannot pass is the
    same failure as one that cannot fail.

    The column also cannot be *neutralised*, which is worth stating because it
    is the first thing anyone will propose. There is no value meaning "this no
    longer decides anything": ``normalize_state`` maps every unrecognised word
    to ``beta``, and ``beta`` is the most permissive state the legacy engine
    has. Writing ``deprecated`` there would widen access. The column can be left
    wrong or dropped; it cannot be made inert by writing to it.

    What makes leaving it wrong safe is :func:`legacy_engine_call_sites` being
    empty — nothing consults ``evaluate_flag``, so the stale word reaches no
    decision. That is checked below rather than assumed, because the day
    somebody adds the first call site is the day the stale column becomes live
    again, and nothing else here would notice.

    ``migrated`` maps capability key to the ``deployment_state`` actually stored
    in the new column. Rows absent from it have not been migrated, and for those
    the legacy word is still the only stored claim there is, so observed drift
    against it blocks exactly as before.
    """
    from services.pulse_control_plane import drift
    from services.pulse_control_plane.capabilities import by_key

    migrated = dict(migrated or {})
    blockers: list[str] = []
    notes: list[str] = []

    result = drift.detect(signals=signals, stored=stored, today=today)
    for finding in result.blocking:
        blockers.append(
            f"STATIC {finding.severity} {finding.kind} "
            f"{finding.capability_key}: {finding.detail}"
        )
    for finding in result.reported:
        if finding.capability_key in migrated:
            # The legacy word is stale by design here; the new column is the
            # stored claim now, and it is checked separately below.
            notes.append(
                f"{finding.capability_key}: legacy state is stale ({finding.kind}) and "
                "inert — migrated, and evaluate_flag has no call sites."
            )
            continue
        blockers.append(
            f"OBSERVED {finding.severity} {finding.kind} "
            f"{finding.capability_key}: {finding.detail}"
        )

    call_sites = legacy_engine_call_sites()
    if call_sites:
        blockers.append(
            "evaluate_flag is called from "
            + ", ".join(call_sites)
            + ". The legacy state column decides something again, and it still holds "
            "the pre-migration words — which for marketplace_checkout means "
            "'internal-only' on a live payment path."
        )

    for key, stored_deployment_state in sorted(migrated.items()):
        capability = by_key(key)
        if capability is None:
            blockers.append(f"MIGRATED UNKNOWN KEY {key}: not in the inventory.")
            continue
        if stored_deployment_state != capability.deployment_state:
            blockers.append(
                f"MIGRATED ROW DIVERGED {key}: column holds "
                f"{stored_deployment_state!r}, this package concluded "
                f"{capability.deployment_state!r}. Either the migration was partial or "
                "the row has been edited since."
            )

    for reconciliation in _reconciliations(signals, stored):
        if reconciliation.inventory_contradicted:
            blockers.append(
                f"INVENTORY CONTRADICTED {reconciliation.capability_key}: measured "
                "production disagrees with this package, so migrating its value would "
                "store a conclusion that is already known to be wrong."
            )

    cells = shadow.compare_all(stored=stored)

    unreviewed = [
        c
        for c in shadow.widenings(cells)
        if (c.capability_key, c.subject_key) not in ACCEPTED_WIDENINGS
    ]
    for cell in unreviewed:
        blockers.append(
            f"UNREVIEWED WIDENING {cell.capability_key}/{cell.subject_key}: "
            f"{cell.legacy_level} -> {cell.candidate_level}. Add it to "
            "ACCEPTED_WIDENINGS with evidence, or explain why the model is wrong."
        )

    for cell in shadow.unexplained(cells):
        blockers.append(
            f"UNEXPLAINED {cell.capability_key}/{cell.subject_key}: the row reconciles "
            "MATCH but the two engines still disagree, so this package differs from the "
            "production it claims to describe."
        )

    if shadow.authentication_gated_cells(cells):
        blockers.append(
            "The control plane has begun resolving anonymous subjects differently from "
            "members. It has no session and cannot decide authentication; something is "
            "inferring it."
        )

    scheduled = {k for w in waves() for k in w.capability_keys}
    overlap = sorted(scheduled & set(NEVER_WIRE))
    if overlap:  # pragma: no cover - Wave.__post_init__ refuses this first
        blockers.append(f"waves schedule never-wire capabilities: {overlap}")

    notes.append(f"{len(scheduled)} capabilities scheduled across {len(waves())} waves.")
    notes.append(f"{len(NEVER_WIRE)} capabilities refused outright: {', '.join(sorted(NEVER_WIRE))}.")
    notes.append(
        f"Shadow: {len(shadow.widenings(cells))} widening cells, "
        f"{len(shadow.narrowings(cells))} narrowing cells, out of {len(cells)}."
    )

    return ActivationReadiness(
        ready=not blockers, blockers=tuple(blockers), notes=tuple(notes)
    )


def plan(result: Optional[ActivationReadiness] = None) -> str:
    """The readiness verdict and the ordered waves, in one printable block."""
    if result is None:
        result = readiness()

    lines = [
        "CONTROL PLANE ACTIVATION — Stage 21 order, Stage 23 gate",
        "",
        f"READY: {'yes' if result.ready else 'NO'}",
        "",
    ]
    if result.blockers:
        lines.append("BLOCKERS")
        lines.extend(f"  - {b}" for b in result.blockers)
        lines.append("")
    lines.append("NOTES")
    lines.extend(f"  - {n}" for n in result.notes)
    lines.append("")

    lines.append("REFUSED OUTRIGHT (not 'later')")
    for key in sorted(NEVER_WIRE):
        lines.append(f"  {key}")
        lines.append(f"    {NEVER_WIRE[key]}")
    lines.append("")

    for wave in waves():
        lines.append(f"WAVE {wave.number} — {wave.name}")
        lines.append(f"  capabilities: {', '.join(wave.capability_keys) or '(none)'}")
        lines.append(f"  purpose:      {wave.purpose}")
        lines.append("  entry:")
        lines.extend(f"    - {c}" for c in wave.entry_criteria)
        lines.append(f"  rollback:     {wave.rollback}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"
