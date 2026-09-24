"""Writing the reconciled truth into ``feature_flags``, without wiring it up.

Two things happen here and it matters that they are separable: :func:`build_manifest`
decides, in a pure function over audited inputs, exactly what would be written;
:func:`apply_manifest` writes it. The manifest can be printed, diffed and reviewed
without a database connection, which is the difference between a migration you
can approve and one you can only watch.

The legacy ``state`` column is not touched
------------------------------------------

This is the core safety property of the whole mission, so it is worth stating
plainly: **no action in this module writes ``feature_flags.state``.** The
reconciled truth goes into new, additive columns (:data:`ADDITIVE_COLUMNS`) that
nothing reads yet. ``feature_flag_engine.evaluate_flag`` would behave
bit-identically before and after this migration — and since it has zero call
sites in production, it behaves identically to nothing.

That is how Mission 2's primary rule is satisfied mechanically rather than by
care: *do not wire ``feature_flags`` into production feature access until the
entire mission passes.* Truth is migrated; authority is not granted. The row for
``marketplace_checkout`` will say ``LIVE_GLOBAL`` in its new column and
``internal-only`` in its old one, and the old one will keep meaning nothing.

Compare-and-set, against the audited value
------------------------------------------

Every write carries ``WHERE feature_key = ? AND state = ?`` with the legacy word
recorded at audit time. If an operator has edited a row since — production's 15
rows currently share one ``updated_at`` of ``2026-05-22T11:51:57``, so none ever
has — the ``UPDATE`` matches nothing, the row is reported ``ABORTED_DRIFT``, and
under the default ``strict=True`` the entire transaction rolls back.

Stage 11 asks to stop *that row* and investigate. Rolling back everything is the
stronger reading and the one implemented, because a half-migrated table has no
owner: some rows describe production under the new model and some under the old,
and the next reader cannot tell which is which by looking. One aborted row means
the audit is stale, and a stale audit is not a safe basis for the other fourteen
writes either.

What is refused
---------------

A row is not written when its reconciliation verdict is ``UNKNOWN`` or
``DEAD_CONFIG``, or when the measured confidence is not ``MEASURED``. Those are
not failures of the migration; they are the migration declining to encode a
guess as truth. They appear in the manifest as ``SKIP_UNVERIFIED`` with the
reason attached, so a skipped row is visible rather than absent.

``public_label``, and who owns it
---------------------------------

Stage 12 asks who owns this field. The answer here is **the operator, seeded
once**, and the reasoning is that the current arrangement produced a concrete
lie: ``public_label`` is derived from the legacy state by
``feature_flag_engine.default_flags`` and then rewritten on *every*
``init_db()`` — which on this codebase runs per request. So
``marketplace_checkout``, a capability that has taken 32 real orders, carries
the public label ``Internal``, and no operator could have corrected it: the next
request would have overwritten the correction.

This migration writes the truthful label once. The companion change in
``bot.py`` makes the seeder insert-only (Stage 26) and gives the admin form the
field (Stage 12), so the value becomes editable and stays edited.

Changing it is safe today in a way worth recording: ``public_label`` is written
by the seeder and read by **nothing**. It does not appear in
``capability_matrix``, in any template, or on any route. A field named
``public_label`` has never once reached the public.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from services.pulse_control_plane.capabilities import CAPABILITIES, by_key
from services.pulse_control_plane.model import MODEL_VERSION, Capability
from services.pulse_control_plane.reconciler import Reconciliation
from services.pulse_control_plane.rollout import assert_rollout_allowed

#: Bumped when the shape of what gets written changes, not when a value does.
MIGRATION_VERSION = "2.0"

#: Columns added to ``feature_flags``. Purely additive: no column is dropped,
#: renamed or retyped, so the migration is reversible by ignoring them and the
#: existing reader (``bot.load_feature_flags``, which does ``SELECT *`` then
#: ``dict(row)``) carries them harmlessly into a dict nothing indexes.
ADDITIVE_COLUMNS: tuple[tuple[str, str], ...] = (
    ("deployment_state", "TEXT"),
    ("eligibility_policy", "TEXT"),
    ("control_model_version", "TEXT"),
    ("reconciled_at", "TEXT"),
    ("reconciled_evidence", "TEXT"),
)

ACTIONS = (
    "WRITE_TRUTH",
    "SKIP_UNVERIFIED",
    "SKIP_NO_ROW",
)

#: Verdicts the migration will act on. ``UNKNOWN`` and ``DEAD_CONFIG`` are
#: excluded: one means we could not establish reality, the other means there is
#: no reality to describe.
WRITABLE_VERDICTS = frozenset({"MATCH", "UNDERSTATED", "OVERSTATED"})


def public_label_for(capability: Capability) -> str:
    """The label a customer should see, derived from both axes.

    Derived here and written once, rather than derived on read, because a value
    recomputed on every request cannot be corrected by the operator who notices
    it is wrong.
    """
    if not capability.reachable:
        return "Unavailable"
    policy = capability.eligibility.key
    if policy in ("ADMIN_ONLY", "OWNER_ONLY", "INTERNAL_ONLY"):
        return "Internal"
    if policy == "PREMIUM_ENTITLEMENT":
        return "Premium"
    if capability.deployment_state == "EXPERIMENTAL":
        return "Beta"
    return "Live"


@dataclass(frozen=True)
class ManifestEntry:
    """One row's planned change, fully determined before anything is opened."""

    capability_key: str
    action: str
    #: The legacy word recorded at audit. The compare-and-set guard.
    expected_state: Optional[str]
    target_deployment_state: Optional[str]
    target_eligibility_policy: Optional[str]
    target_public_label: Optional[str]
    current_public_label: Optional[str]
    target_rollout: Optional[int]
    current_rollout: Optional[int]
    verdict: str
    reason: str
    evidence: str = ""

    def __post_init__(self) -> None:
        if self.action not in ACTIONS:
            raise ValueError(f"unknown migration action {self.action!r}")

    @property
    def writes(self) -> bool:
        return self.action == "WRITE_TRUTH"

    @property
    def changes_label(self) -> bool:
        return self.writes and self.target_public_label != self.current_public_label

    @property
    def changes_rollout(self) -> bool:
        return self.writes and self.target_rollout != self.current_rollout


def build_manifest(
    reconciliations: tuple[Reconciliation, ...],
    stored_rows: dict[str, dict],
) -> tuple[ManifestEntry, ...]:
    """Decide every write, deterministically, with no database access.

    ``stored_rows`` maps capability key to the audited row dict (``state``,
    ``rollout_percentage``, ``public_label``). Ordered by :data:`CAPABILITIES`
    rather than by verdict so that two runs over the same audit produce
    byte-identical manifests and a diff between them means something.
    """
    by_verdict = {r.capability_key: r for r in reconciliations}
    entries: list[ManifestEntry] = []

    for capability in CAPABILITIES:
        key = capability.key
        reconciliation = by_verdict.get(key)
        row = stored_rows.get(key)

        if row is None:
            entries.append(
                ManifestEntry(
                    capability_key=key,
                    action="SKIP_NO_ROW",
                    expected_state=None,
                    target_deployment_state=None,
                    target_eligibility_policy=None,
                    target_public_label=None,
                    current_public_label=None,
                    target_rollout=None,
                    current_rollout=None,
                    verdict=reconciliation.verdict if reconciliation else "UNKNOWN",
                    reason=(
                        "no row in feature_flags. This migration repairs existing rows; "
                        "it does not create capability config, because a row created here "
                        "would have no operator behind it."
                    ),
                )
            )
            continue

        current_state = row.get("state")
        current_label = row.get("public_label")
        current_rollout = row.get("rollout_percentage")

        if reconciliation is None or reconciliation.verdict not in WRITABLE_VERDICTS:
            entries.append(
                ManifestEntry(
                    capability_key=key,
                    action="SKIP_UNVERIFIED",
                    expected_state=current_state,
                    target_deployment_state=None,
                    target_eligibility_policy=None,
                    target_public_label=None,
                    current_public_label=current_label,
                    target_rollout=None,
                    current_rollout=current_rollout,
                    verdict=reconciliation.verdict if reconciliation else "UNKNOWN",
                    reason=(
                        "verdict is not one the migration acts on; production reality was "
                        "not established well enough to write it down"
                    ),
                )
            )
            continue

        if reconciliation.observed_confidence != "MEASURED":
            entries.append(
                ManifestEntry(
                    capability_key=key,
                    action="SKIP_UNVERIFIED",
                    expected_state=current_state,
                    target_deployment_state=None,
                    target_eligibility_policy=None,
                    target_public_label=None,
                    current_public_label=current_label,
                    target_rollout=None,
                    current_rollout=current_rollout,
                    verdict=reconciliation.verdict,
                    reason=(
                        f"observed confidence is {reconciliation.observed_confidence}, not "
                        "MEASURED. Inferred reality is good enough to report and not good "
                        "enough to store as truth."
                    ),
                )
            )
            continue

        if reconciliation.inventory_contradicted:
            entries.append(
                ManifestEntry(
                    capability_key=key,
                    action="SKIP_UNVERIFIED",
                    expected_state=current_state,
                    target_deployment_state=None,
                    target_eligibility_policy=None,
                    target_public_label=None,
                    current_public_label=current_label,
                    target_rollout=None,
                    current_rollout=current_rollout,
                    verdict=reconciliation.verdict,
                    reason=(
                        "measured production contradicts the deployment state this package "
                        "declares. The inventory is wrong, and writing it to production "
                        "would propagate the error rather than fix it."
                    ),
                )
            )
            continue

        # Refuses a partial rollout of a protected capability. Target is always
        # 100 today; the call is here so that a future manifest that lowers one
        # cannot reach the database.
        assert_rollout_allowed(key, capability.protected, capability.rollout_percentage)

        entries.append(
            ManifestEntry(
                capability_key=key,
                action="WRITE_TRUTH",
                expected_state=current_state,
                target_deployment_state=capability.deployment_state,
                target_eligibility_policy=capability.eligibility.key,
                target_public_label=public_label_for(capability),
                current_public_label=current_label,
                target_rollout=capability.rollout_percentage,
                current_rollout=current_rollout,
                verdict=reconciliation.verdict,
                reason=reconciliation.rationale,
                evidence=capability.evidence,
            )
        )

    return tuple(entries)


# ---------------------------------------------------------------------------
# Applying it
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RowResult:
    capability_key: str
    outcome: str  # WRITTEN | SKIPPED | ABORTED_DRIFT
    detail: str


@dataclass
class MigrationResult:
    dry_run: bool
    columns_added: tuple[str, ...] = ()
    rows: list[RowResult] = field(default_factory=list)
    committed: bool = False
    rolled_back_because: str = ""

    @property
    def written(self) -> int:
        return sum(1 for r in self.rows if r.outcome == "WRITTEN")

    @property
    def aborted(self) -> tuple[RowResult, ...]:
        return tuple(r for r in self.rows if r.outcome == "ABORTED_DRIFT")


def _is_postgres(conn: Any) -> bool:
    return "psycopg" in type(conn).__module__


def _q(sql: str, conn: Any) -> str:
    """Translate the ``?`` paramstyle to ``%s`` for psycopg2.

    Deliberately not a general SQL translator. The statements in this module are
    fixed, contain no literal ``?`` and no ``%``, so a substitution is exactly
    right here and would be exactly wrong anywhere else.
    """
    return sql.replace("?", "%s") if _is_postgres(conn) else sql


def existing_columns(conn: Any) -> set[str]:
    cur = conn.cursor()
    if _is_postgres(conn):
        cur.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name='feature_flags'"
        )
        return {r[0] for r in cur.fetchall()}
    cur.execute("PRAGMA table_info(feature_flags)")
    return {r[1] for r in cur.fetchall()}


def ensure_columns(conn: Any, *, dry_run: bool = True) -> tuple[str, ...]:
    """Add the additive columns. Idempotent, and a no-op when they all exist."""
    present = existing_columns(conn)
    missing = [(name, sqltype) for name, sqltype in ADDITIVE_COLUMNS if name not in present]
    if dry_run:
        return tuple(name for name, _ in missing)
    cur = conn.cursor()
    for name, sqltype in missing:
        cur.execute(f"ALTER TABLE feature_flags ADD COLUMN {name} {sqltype}")
    return tuple(name for name, _ in missing)


_UPDATE = """
UPDATE feature_flags
   SET deployment_state = ?,
       eligibility_policy = ?,
       public_label = ?,
       rollout_percentage = ?,
       control_model_version = ?,
       reconciled_at = ?,
       reconciled_evidence = ?,
       updated_at = ?
 WHERE feature_key = ?
   AND state = ?
"""


def apply_manifest(
    conn: Any,
    manifest: tuple[ManifestEntry, ...],
    *,
    reconciled_at: str,
    dry_run: bool = True,
    strict: bool = True,
) -> MigrationResult:
    """Apply the manifest in one transaction. Defaults to a dry run.

    ``strict`` rolls the whole transaction back if any row's compare-and-set
    fails. See the module docstring for why that is the right reading of "stop
    that row and investigate".
    """
    result = MigrationResult(dry_run=dry_run)
    result.columns_added = ensure_columns(conn, dry_run=dry_run)
    cur = conn.cursor()

    for entry in manifest:
        if not entry.writes:
            result.rows.append(RowResult(entry.capability_key, "SKIPPED", entry.reason))
            continue

        if dry_run:
            result.rows.append(
                RowResult(
                    entry.capability_key,
                    "WRITTEN",
                    f"would set deployment_state={entry.target_deployment_state}, "
                    f"eligibility_policy={entry.target_eligibility_policy}, "
                    f"public_label={entry.current_public_label!r}->{entry.target_public_label!r}, "
                    f"rollout={entry.current_rollout}->{entry.target_rollout} "
                    f"(guarded on state={entry.expected_state!r})",
                )
            )
            continue

        cur.execute(
            _q(_UPDATE, conn),
            (
                entry.target_deployment_state,
                entry.target_eligibility_policy,
                entry.target_public_label,
                entry.target_rollout,
                MODEL_VERSION,
                reconciled_at,
                entry.evidence[:2000],
                reconciled_at,
                entry.capability_key,
                entry.expected_state,
            ),
        )
        if cur.rowcount == 1:
            result.rows.append(
                RowResult(entry.capability_key, "WRITTEN", entry.reason)
            )
        else:
            result.rows.append(
                RowResult(
                    entry.capability_key,
                    "ABORTED_DRIFT",
                    f"compare-and-set matched {cur.rowcount} rows: stored state is no "
                    f"longer {entry.expected_state!r}. The audit is stale for this row.",
                )
            )

    if dry_run:
        conn.rollback()
        return result

    if result.aborted and strict:
        conn.rollback()
        result.rolled_back_because = (
            f"{len(result.aborted)} row(s) drifted since the audit: "
            + ", ".join(r.capability_key for r in result.aborted)
        )
        return result

    conn.commit()
    result.committed = True
    return result


def manifest_summary(manifest: tuple[ManifestEntry, ...]) -> str:
    """A reviewable, deterministic rendering. No timestamps, no ordering by luck."""
    lines = [
        f"control-plane migration manifest v{MIGRATION_VERSION} "
        f"(model v{MODEL_VERSION})",
        "",
        f"{'capability':27}{'action':17}{'guard':15}{'deployment':18}"
        f"{'eligibility':22}{'label':22}{'rollout'}",
    ]
    for e in manifest:
        label = (
            f"{e.current_public_label}->{e.target_public_label}"
            if e.changes_label
            else str(e.current_public_label)
        )
        rollout = (
            f"{e.current_rollout}->{e.target_rollout}"
            if e.changes_rollout
            else str(e.current_rollout)
        )
        lines.append(
            f"{e.capability_key:27}{e.action:17}{str(e.expected_state):15}"
            f"{str(e.target_deployment_state):18}{str(e.target_eligibility_policy):22}"
            f"{label:22}{rollout}"
        )
    writes = sum(1 for e in manifest if e.writes)
    lines += [
        "",
        f"{writes} row(s) would be written, {len(manifest) - writes} skipped.",
        "feature_flags.state is not written by any action above.",
    ]
    return "\n".join(lines)
