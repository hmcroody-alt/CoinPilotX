"""Drift detection, split by what a failure would actually mean.

Two kinds of drift, and one gate that treats them differently
--------------------------------------------------------------

**STATIC CONTRACT DRIFT** is computable from the repository alone. The inventory
lost a key, a capability arrived without evidence, the migration started writing
the legacy column, a legacy word became parseable again. Every one of these is a
statement about the commit under test, reproducible on any machine with no
network and no database. A build may fail on it, because the person who broke it
is the person whose change is being tested.

**PRODUCTION OBSERVED DRIFT** is the stored configuration disagreeing with
measured production. ``marketplace_checkout`` is seeded ``internal-only`` while
checkout has taken 32 real orders; that is drift, it is CRITICAL, and it is
*not* the fault of whatever pull request happens to be open. Failing builds on
it would mean a launch in production turns every unrelated PR red until somebody
edits a config row — which trains people to bypass the gate, and a gate people
route around is worse than no gate, because it still looks like coverage.

There is a second, harder reason the split is not optional. A CI runner cannot
reach the production database. So the "production" side of this gate is not
reading production at all — it is reading
:mod:`services.pulse_control_plane.observations`, a recording dated
``MEASURED_AT``. Failing a build on it would be failing on the age of a file.
The recording is still worth checking, and it is still reported with full
severity; it just cannot be the thing that blocks a merge.

So: :func:`gate` blocks on CRITICAL and HIGH **static** findings, and reports
everything else. The report is the deliverable for the observed half.

Why staleness is MEDIUM
-----------------------

``MEASUREMENT_STALE`` is the one static finding that depends on the clock rather
than on the tree, so it would eventually turn red on a day when nobody changed
anything. Capping it at MEDIUM keeps it below the blocking line, which is the
honest place for "somebody should re-measure soon" to sit.
"""

from __future__ import annotations

import functools
import logging
import pathlib
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Optional

from services.pulse_control_plane import capabilities as caps
from services.pulse_control_plane import env_gates
from services.pulse_control_plane import migration as mig
from services.pulse_control_plane import observations as obs
from services.pulse_control_plane.model import UNGATED_POLICIES
from services.pulse_control_plane.parsing import parse_deployment_state
from services.pulse_control_plane.reconciler import (
    Reconciliation,
    Signal,
    orphaned_config_keys,
    reconcile_all,
)

#: Highest first. Used for ordering and for the blocking comparison, so the
#: order is load-bearing rather than cosmetic.
SEVERITIES = ("CRITICAL", "HIGH", "MEDIUM", "LOW")

DRIFT_CLASSES = ("STATIC_CONTRACT", "PRODUCTION_OBSERVED")

#: Static findings at or above this severity fail the build. Production-observed
#: findings never do, whatever their severity — see the module docstring.
BLOCKING_SEVERITIES = frozenset({"CRITICAL", "HIGH"})

#: How old a production measurement may be before it is called out. Two weeks:
#: long enough that a normal sprint does not trip it, short enough that a
#: quarter-old measurement cannot quietly keep passing as evidence.
MEASUREMENT_MAX_AGE_DAYS = 14


@dataclass(frozen=True)
class DriftFinding:
    """One thing that does not line up, and what it would take to fix it.

    ``remedy`` is required rather than optional. A drift report whose entries
    say only what is wrong gets read once and then filtered out of the build
    log; one that says what to do about each line gets acted on. It is also a
    check on the finding itself — a condition nobody can describe a fix for is
    usually not a real finding.
    """

    capability_key: str
    kind: str
    drift_class: str
    severity: str
    detail: str
    remedy: str

    def __post_init__(self) -> None:
        if self.drift_class not in DRIFT_CLASSES:
            raise ValueError(f"unknown drift class {self.drift_class!r}")
        if self.severity not in SEVERITIES:
            raise ValueError(f"unknown severity {self.severity!r}")
        if not self.remedy.strip():
            raise ValueError(f"{self.kind!r}: a finding must say what would fix it")

    @property
    def blocking(self) -> bool:
        return (
            self.drift_class == "STATIC_CONTRACT"
            and self.severity in BLOCKING_SEVERITIES
        )

    def _rank(self) -> tuple:
        return (SEVERITIES.index(self.severity), self.drift_class, self.kind, self.capability_key)


# ---------------------------------------------------------------------------
# Static contract drift — the repository against itself
# ---------------------------------------------------------------------------


def static_findings(today: Optional[date] = None) -> tuple[DriftFinding, ...]:
    """Everything checkable without a network or a database.

    ``today`` is injected rather than read from the clock so that the staleness
    check is testable in both directions. A test that can only ever observe
    "not stale" is not testing the staleness rule.
    """
    findings: list[DriftFinding] = []
    inventory = {c.key: c for c in caps.CAPABILITIES}

    # --- the inventory and the seeded rows must describe the same fifteen ---
    for key in sorted(set(caps.SEEDED_STATES) - set(inventory)):
        findings.append(
            DriftFinding(
                key,
                "SEEDED_KEY_NOT_IN_INVENTORY",
                "STATIC_CONTRACT",
                "HIGH",
                f"{key!r} has a seeded feature_flags row but no reconciled capability",
                "add it to capabilities.CAPABILITIES with evidence, or remove the seed",
            )
        )
    for key in sorted(set(inventory) - set(caps.SEEDED_STATES)):
        findings.append(
            DriftFinding(
                key,
                "INVENTORY_KEY_NOT_SEEDED",
                "STATIC_CONTRACT",
                "MEDIUM",
                f"{key!r} is reconciled here but no row is seeded for it",
                "seed the row in feature_flag_engine, or drop the capability",
            )
        )

    for key, capability in sorted(inventory.items()):
        # --- every row must carry its evidence ---
        if len(capability.evidence.strip()) < 40:
            findings.append(
                DriftFinding(
                    key,
                    "CAPABILITY_WITHOUT_EVIDENCE",
                    "STATIC_CONTRACT",
                    "HIGH",
                    f"{key!r} states a deployment verdict on {len(capability.evidence.strip())} characters of evidence",
                    "cite the route probe, source guard, env gate or row count that supports it",
                )
            )

        # --- the invariant that caught Mission 1, re-checked in bulk ---
        #
        # ``Capability.__post_init__`` already refuses this pairing, so a
        # finding here means somebody constructed the row another way — by
        # mutating a frozen dataclass through ``object.__setattr__``, or by
        # loading it from data. Cheap to check, and the one it guards against
        # is the exact sentence this package was built to make unwriteable.
        if capability.deployment_state == "LIVE_CONDITIONAL" and (
            capability.eligibility.key in UNGATED_POLICIES
        ):
            findings.append(
                DriftFinding(
                    key,
                    "CONDITIONAL_WITHOUT_AUTHORITY",
                    "STATIC_CONTRACT",
                    "CRITICAL",
                    f"{key!r} is LIVE_CONDITIONAL behind {capability.eligibility.key}, which gates nobody",
                    "name the authority that decides, or restate the row as LIVE_GLOBAL",
                )
            )

        # --- a protected capability may not be half-deployed ---
        if capability.protected and capability.rollout_percentage != 100:
            findings.append(
                DriftFinding(
                    key,
                    "PROTECTED_CAPABILITY_PARTIALLY_ROLLED_OUT",
                    "STATIC_CONTRACT",
                    "CRITICAL",
                    f"{key!r} protects {', '.join(capability.protected)} at {capability.rollout_percentage}% rollout",
                    "protected domains are all-or-nothing; set 100 or take the capability down",
                )
            )

    # --- the two vocabularies must stay apart ---
    #
    # If a legacy word starts parsing as a deployment state, the columns have
    # been re-merged and ``admin_command`` can be stored as ``enabled`` again —
    # the single state meaning "unconditionally visible and usable", on 199
    # admin-guarded routes.
    for word in sorted(set(caps.SEEDED_STATES.values())):
        if word == "disabled":
            # The one word both vocabularies legitimately share, with the same
            # meaning in each.
            continue
        if _parses_quietly(word):
            findings.append(
                DriftFinding(
                    "-",
                    "LEGACY_WORD_PARSES_AT_RUNTIME",
                    "STATIC_CONTRACT",
                    "CRITICAL",
                    f"the legacy word {word!r} now resolves as a deployment state",
                    "keep the legacy vocabulary confined to migration.py; it must not resolve at runtime",
                )
            )

    # --- the mission's primary rule, checked mechanically ---
    for column in _migration_written_columns():
        if column == "state":
            findings.append(
                DriftFinding(
                    "-",
                    "MIGRATION_WRITES_LEGACY_STATE",
                    "STATIC_CONTRACT",
                    "CRITICAL",
                    "the migration's UPDATE assigns feature_flags.state",
                    "truth goes in the additive columns; the legacy column stays frozen until activation",
                )
            )

    # --- the dead-gate catalog must stay true ---
    #
    # This check outlived the act it was written for, and its meaning changed
    # when that act completed.
    #
    # Before 2026-09-24 every entry in env_gates.DEAD_GATES said "delete this,
    # nothing reads it", and a new reader turned that recommendation into a
    # proposal to delete a live gate while still reading as a tidy-up.
    #
    # The fourteen are now gone from Railway, which makes a new reader worse
    # rather than moot. Code that reads a name production no longer sets does not
    # fail; it silently takes its default. So the failure is a feature quietly
    # running in its fallback configuration, with a variable in the source that
    # an operator can search for, find, and never locate in the dashboard.
    for gate_name in sorted(g.name for g in env_gates.DEAD_GATES):
        readers = _readers_of(gate_name)
        if readers:
            findings.append(
                DriftFinding(
                    "-",
                    "DEAD_GATE_GAINED_A_READER",
                    "STATIC_CONTRACT",
                    "HIGH",
                    f"{gate_name} was retired from Railway on {env_gates.RETIRED_AT} "
                    f"but is now read by {', '.join(readers)}",
                    "the variable is not set in production, so this reader silently takes its "
                    "default; either give the gate a name that exists or restore it with "
                    "env_gates.removal_plan()'s rollback half",
                )
            )

    # --- how old the production recording is ---
    measured = datetime.strptime(obs.MEASURED_AT, "%Y-%m-%d").date()
    today = today or datetime.now(timezone.utc).date()
    age = (today - measured).days
    if age > MEASUREMENT_MAX_AGE_DAYS:
        findings.append(
            DriftFinding(
                "-",
                "MEASUREMENT_STALE",
                "STATIC_CONTRACT",
                "MEDIUM",
                f"production was last measured {age} days ago ({obs.MEASURED_AT}, {obs.MEASURED_AGAINST})",
                "re-probe the routes and re-count the tables, then update observations.py",
            )
        )

    return tuple(sorted(findings, key=DriftFinding._rank))


#: Where a gate could acquire a reader. ``scripts/`` and ``tests/`` are excluded
#: deliberately: an audit script that *names* a variable in order to report on it
#: is not gated by it, and counting it as a reader would keep every dead gate
#: alive in the inventory forever. ``UNDX_METRICS_ENABLED`` is exactly this case.
_READER_ROOTS = ("services", "bot.py", "undx_router.py", "undx_execution_kernel.py")


@functools.lru_cache(maxsize=1)
def _searchable_sources() -> tuple[tuple[str, str], ...]:
    """(relative path, text) for every place a gate could be read.

    Substring matching, rather than matching a literal getenv call. That choice
    is Mission 1's, and it is load-bearing: this codebase reads variables
    through at least four indirections plus one name assembled at runtime, and
    the precise version reported 51 dead variables of which 38 were alive —
    including ``MARKETPLACE_CARD_PAYMENTS_ENABLED``, the gate holding checkout
    open. Over-counting readers leaves a stale variable for a human to check;
    under-counting deletes a live kill switch.

    This paragraph used to spell that call out in full, with a placeholder
    variable name in the quotes. ``test_every_variable_production_code_reads_is_documented``
    scans source text, found the placeholder, and failed the protection suite
    demanding that a variable called NAME be documented in ``.env.example``.

    Which is the fourth time this package has met the same mistake, and the
    first time it was aimed at the package rather than made by it: an auditor
    naming a variable is not a reader of it, a catalog describing a gate is not
    a gate, a definition is not a call site — and an example in a docstring is
    not an environment read. The phrasing above avoids the trigger rather than
    fixing the scanner, because that scanner guards the whole repository and a
    change to it belongs on its own.
    """
    root = pathlib.Path(__file__).resolve().parents[2]
    out: list[tuple[str, str]] = []
    for entry in _READER_ROOTS:
        target = root / entry
        if target.is_file():
            out.append((entry, target.read_text(errors="ignore")))
        elif target.is_dir():
            for path in sorted(target.rglob("*.py")):
                relative = path.relative_to(root)
                # This package lives under services/ and names every dead gate
                # in order to describe it. Counting itself as a reader would
                # make the check fire on all fourteen on a healthy tree — which
                # is precisely the mistake the check exists to catch, one level
                # up: a describer is not a reader.
                if relative.parts[:2] == ("services", "pulse_control_plane"):
                    continue
                out.append((str(relative), path.read_text(errors="ignore")))
    return tuple(out)


def _readers_of(name: str) -> tuple[str, ...]:
    return tuple(path for path, text in _searchable_sources() if name in text)


def _parses_quietly(word: str) -> bool:
    """Ask whether a legacy word resolves, without logging that it did not.

    :func:`~services.pulse_control_plane.parsing.parse_deployment_state` warns
    on every rejection, which is right when it is parsing stored data and wrong
    here: the *expected* result of this probe is rejection, three times per run,
    so a healthy tree would print three warnings that look like a fault. A
    detector whose clean output contains warnings teaches people to ignore the
    warnings.
    """
    logger = logging.getLogger("services.pulse_control_plane.parsing")
    previous = logger.disabled
    logger.disabled = True
    try:
        return parse_deployment_state(word).accepted
    finally:
        logger.disabled = previous


def _migration_written_columns() -> set[str]:
    """Columns the migration's UPDATE assigns.

    Parsed out of the statement rather than matched against it, because
    ``deployment_state = ?`` contains ``state = ?`` as a substring: the naive
    check passes while the legacy column is being overwritten, which is the one
    failure mode this is here to catch.
    """
    statement = mig._UPDATE
    set_clause = statement.split("SET", 1)[1].split("WHERE", 1)[0]
    columns = set()
    for assignment in set_clause.split(","):
        name = assignment.split("=", 1)[0].strip()
        if name:
            columns.add(name)
    return columns


# ---------------------------------------------------------------------------
# Production observed drift — stored configuration against measurement
# ---------------------------------------------------------------------------


def observed_findings(
    signals: Optional[dict[str, tuple[Signal, ...]]] = None,
    stored: Optional[dict[str, str]] = None,
) -> tuple[DriftFinding, ...]:
    """Stored ``feature_flags`` rows against what production was measured doing.

    Both arguments are injectable for the same reason
    :func:`~services.pulse_control_plane.reconciler.reconcile` takes signals
    rather than importing them: a detector that can only run against the one
    recording shipped in this repository cannot be shown to detect anything.
    """
    signals = obs.PRODUCTION_SIGNALS if signals is None else signals
    stored = obs.STORED_STATES if stored is None else stored

    findings: list[DriftFinding] = []
    for rec in reconcile_all(signals, stored):
        findings.extend(_findings_for(rec))

    for key in orphaned_config_keys(stored):
        findings.append(
            DriftFinding(
                key,
                "ORPHANED_CONFIG_ROW",
                "PRODUCTION_OBSERVED",
                "LOW",
                f"{key!r} has a stored row but no capability describes it",
                "delete the row, or reconcile the capability it refers to",
            )
        )

    return tuple(sorted(findings, key=DriftFinding._rank))


_VERDICT_REMEDY = {
    "OVERSTATED": (
        "the stored row claims more exposure than production has; correct the row "
        "before anything reads it, or the wiring will grant access nobody verified"
    ),
    "UNDERSTATED": (
        "production is more exposed than the stored row says; wiring this row as-is "
        "would withdraw a live capability from real users"
    ),
    "UNKNOWN": "the signals contradict each other or are too weak; re-measure before deciding",
    "DEAD_CONFIG": "nothing reads this row; remove it or wire it deliberately",
    "LIVE_WITHOUT_CONFIG": "production runs this with no stored row at all; seed one",
}


def _findings_for(rec: Reconciliation) -> list[DriftFinding]:
    out: list[DriftFinding] = []
    if rec.verdict != "MATCH":
        out.append(
            DriftFinding(
                rec.capability_key,
                f"CONFIG_{rec.verdict}",
                "PRODUCTION_OBSERVED",
                rec.severity,
                rec.rationale,
                _VERDICT_REMEDY[rec.verdict],
            )
        )
    if rec.inventory_contradicted:
        # Distinct from config drift and more serious than most of it: the
        # stored row being wrong is the problem this package describes, but the
        # *inventory* being wrong means the description is the thing that
        # drifted, and every verdict derived from it is suspect.
        out.append(
            DriftFinding(
                rec.capability_key,
                "INVENTORY_CONTRADICTED",
                "PRODUCTION_OBSERVED",
                "CRITICAL",
                f"measured production disagrees with what capabilities.py declares: {rec.rationale}",
                "re-reconcile the capability against the measurement; this package is the thing that is wrong",
            )
        )
    return out


# ---------------------------------------------------------------------------
# Stage 17/18 — the report and the gate
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GateResult:
    """The outcome of one drift evaluation.

    ``passed`` answers only "may this build proceed". It is deliberately not
    "is everything fine": a run with four CRITICAL production findings passes,
    and says so in the report. Conflating the two is how an observed-drift
    failure would end up disabling the static half as well.
    """

    passed: bool
    blocking: tuple[DriftFinding, ...]
    reported: tuple[DriftFinding, ...]

    @property
    def findings(self) -> tuple[DriftFinding, ...]:
        return tuple(sorted(self.blocking + self.reported, key=DriftFinding._rank))


def gate(findings: tuple[DriftFinding, ...]) -> GateResult:
    blocking = tuple(f for f in findings if f.blocking)
    reported = tuple(f for f in findings if not f.blocking)
    return GateResult(not blocking, blocking, reported)


def detect(
    *,
    signals: Optional[dict[str, tuple[Signal, ...]]] = None,
    stored: Optional[dict[str, str]] = None,
    today: Optional[date] = None,
) -> GateResult:
    """Run both halves. Read-only: this function opens nothing and writes nothing."""
    return gate(static_findings(today=today) + observed_findings(signals, stored))


def report(result: GateResult) -> str:
    """A deterministic, byte-stable rendering.

    Stable ordering matters more than it looks: a CI comment that reshuffles
    between runs cannot be diffed, so a reviewer cannot tell a new finding from
    a moved one, and the whole report gets skimmed instead of read.
    """
    lines = [
        "CAPABILITY DRIFT REPORT",
        f"measured: {obs.MEASURED_AT} ({obs.MEASURED_AGAINST})",
        f"model:    {mig.MIGRATION_VERSION}",
        "",
    ]

    def section(title: str, entries: tuple[DriftFinding, ...]) -> None:
        lines.append(title)
        if not entries:
            lines.append("  (none)")
        for finding in entries:
            lines.append(f"  [{finding.severity}] {finding.kind} — {finding.capability_key}")
            lines.append(f"      {finding.detail}")
            lines.append(f"      fix: {finding.remedy}")
        lines.append("")

    all_findings = result.findings
    section(
        "STATIC CONTRACT DRIFT (blocks the build)",
        tuple(f for f in all_findings if f.drift_class == "STATIC_CONTRACT"),
    )
    section(
        "PRODUCTION OBSERVED DRIFT (reported, never blocks)",
        tuple(f for f in all_findings if f.drift_class == "PRODUCTION_OBSERVED"),
    )

    counts = {s: sum(1 for f in all_findings if f.severity == s) for s in SEVERITIES}
    lines.append(
        "severity: " + "  ".join(f"{s}={counts[s]}" for s in SEVERITIES)
    )
    lines.append(f"gate: {'PASS' if result.passed else 'FAIL'} ({len(result.blocking)} blocking)")
    return "\n".join(lines)
